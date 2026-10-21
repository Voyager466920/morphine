import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from morphine import config, guard, install, state  # noqa: E402
from morphine.adapters import codex, gemini, get_usage  # noqa: E402
from morphine.adapters.antigravity import usage_from_status  # noqa: E402
from morphine.adapters.base import Usage, Window  # noqa: E402
from morphine.adapters.claude import usage_from_rate_limits  # noqa: E402
from morphine.util import parse_ts  # noqa: E402
from morphine.wrapper import LimitDetector, parse_reset  # noqa: E402


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            "MORPHINE_HOME": str(self.home / "m"),
            "CLAUDE_CONFIG_DIR": str(self.home / "claude"),
            "CODEX_HOME": str(self.home / "codex"),
            "GEMINI_HOME": str(self.home / "gemini"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class TestParsing(unittest.TestCase):
    def test_parse_ts_forms(self):
        self.assertEqual(parse_ts(1700000000), 1700000000.0)
        self.assertEqual(parse_ts(1700000000000), 1700000000.0)
        self.assertEqual(parse_ts("1700000000"), 1700000000.0)
        self.assertAlmostEqual(parse_ts("2023-11-14T22:13:20Z"), 1700000000.0)
        self.assertAlmostEqual(parse_ts("2023-11-14T22:13:20.123456789+00:00"), 1700000000.123456, places=3)
        self.assertAlmostEqual(parse_ts("2023-11-15T07:13:20+0900"), 1700000000.0)
        self.assertIsNone(parse_ts("not a date"))
        self.assertIsNone(parse_ts(None))

    def test_claude_statusline_and_oauth_shapes(self):
        sl = usage_from_rate_limits({"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600}}, "statusline")
        self.assertEqual(sl.windows[0].name, "5h")
        self.assertEqual(sl.windows[0].used_pct, 23.5)
        oa = usage_from_rate_limits({
            "five_hour": {"utilization": 97.0, "resets_at": "2025-01-01T05:00:00Z"},
            "seven_day": {"utilization": 40, "resets_at": "2025-01-05T00:00:00Z"},
        }, "oauth")
        self.assertEqual([w.name for w in oa.windows], ["5h", "7d"])
        self.assertEqual(oa.windows[0].used_pct, 97.0)

    def test_antigravity_picks_worst_pool(self):
        data = {"userStatus": {"pools": [
            {"name": "a", "remainingFraction": 0.6, "resetTime": "2030-01-01T00:00:00Z"},
            {"name": "b", "remainingFraction": 0.01, "resetTime": "2030-01-01T01:00:00Z"},
        ]}}
        u = usage_from_status(data)
        self.assertAlmostEqual(u.windows[0].used_pct, 99.0)
        self.assertEqual(u.windows[0].resets_at, parse_ts("2030-01-01T01:00:00Z"))

    def test_parse_reset_variants(self):
        base = time.mktime((2026, 10, 21, 10, 0, 0, 0, 0, -1))
        self.assertAlmostEqual(parse_reset("try again in 2 hours 13 minutes", base), base + 2 * 3600 + 13 * 60)
        self.assertAlmostEqual(parse_reset("please retry in 41.5s.", base), base + 41.5)
        self.assertAlmostEqual(parse_reset("try again in 1h 5m", base), base + 3900)
        at3pm = parse_reset("claude usage limit reached. your limit will reset at 3pm (asia/seoul).", base)
        self.assertEqual(dt.datetime.fromtimestamp(at3pm).hour, 15)
        bare = parse_reset("5-hour limit reached ∙ resets 9:30am", base)
        d = dt.datetime.fromtimestamp(bare)
        self.assertEqual((d.hour, d.minute, d.day), (9, 30, 22))  # tomorrow: 9:30 already passed
        iso = parse_reset("quota resets at 2030-01-01T00:00:00Z", base)
        self.assertEqual(iso, parse_ts("2030-01-01T00:00:00Z"))
        self.assertIsNone(parse_reset("usage limit reached", base))

    def test_detector_strips_ansi(self):
        d = LimitDetector(config.DEFAULTS["wrapper"]["limit_patterns"])
        self.assertIsNone(d.feed(b"all good\n"))
        self.assertIsNotNone(d.feed(b"\x1b[31mYou've hit your \x1b[1musage limit\x1b[0m"))


class TestDecision(Env):
    def test_threshold(self):
        cfg = config.load()
        t = 1_000_000.0
        ok = Usage("claude", [Window("5h", 94.0, t + 600)])
        self.assertIsNone(guard.check("claude", ok, cfg, at=t))
        low = Usage("claude", [Window("5h", 98.5, t + 600), Window("7d", 50.0, t + 9e5)])
        d = guard.check("claude", low, cfg, at=t)
        self.assertIsNotNone(d)
        self.assertEqual(d.window, "5h")
        self.assertAlmostEqual(d.resume_at, t + 600 + cfg["resume_grace_sec"])

    def test_weekly_wins_when_both_drained(self):
        cfg = config.load()
        t = 1_000_000.0
        u = Usage("codex", [Window("5h", 99.0, t + 600), Window("7d", 99.5, t + 90_000)])
        d = guard.check("codex", u, cfg, at=t)
        self.assertAlmostEqual(d.resume_at, t + 90_000 + cfg["resume_grace_sec"])

    def test_past_reset_counts_as_fresh(self):
        cfg = config.load()
        t = 1_000_000.0
        u = Usage("claude", [Window("5h", 100.0, t - 1)])
        self.assertIsNone(guard.check("claude", u, cfg, at=t))

    def test_unknown_reset_waits_fallback(self):
        cfg = config.load()
        t = 1_000_000.0
        d = guard.check("claude", Usage("claude", [Window("5h", 99.9, None)]), cfg, at=t)
        self.assertAlmostEqual(d.resume_at, t + 60 * 31)
        self.assertFalse(d.known_reset)

    def test_estimates_do_not_lock_by_default(self):
        cfg = config.load()
        t = 1_000_000.0
        u = Usage("gemini", [Window("1d", 99.9, t + 100, estimated=True)])
        self.assertIsNone(guard.check("gemini", u, cfg, at=t))
        config.set_value("providers.gemini.lockout_on_estimate", "true")
        self.assertIsNotNone(guard.check("gemini", u, config.load(), at=t))

    def test_unwatched_window_ignored(self):
        config.set_value("windows", '["5h"]')
        t = 1_000_000.0
        u = Usage("claude", [Window("7d", 99.9, t + 100)])
        self.assertIsNone(guard.check("claude", u, config.load(), at=t))


class TestWait(Env):
    def test_wait_then_resume_and_state(self):
        cfg = dict(config.load(), poll_sec=1, keep_awake=False, notify=False)
        t = time.time()
        d = guard.Decision(resume_at=t + 1.5, reason="5h 99% used", window="5h")
        seen = []
        with mock.patch("morphine.guard.get_usage", return_value=Usage("claude", [Window("5h", 99, t + 1)])):
            out = guard.wait_out("claude", d, cfg, sleep=lambda s: (seen.append(len(state.waiting())), time.sleep(s)))
        self.assertEqual(out, "resumed")
        self.assertTrue(seen and seen[0] == 1)
        self.assertEqual(state.waiting(), [])

    def test_resume_now_flag(self):
        cfg = dict(config.load(), poll_sec=1, keep_awake=False, notify=False)
        d = guard.Decision(resume_at=time.time() + 3600, reason="x", window="5h")

        def fake_sleep(s):
            state.request_resume_now()
            time.sleep(0.05)

        self.assertEqual(guard.wait_out("claude", d, cfg, sleep=fake_sleep), "resumed early")

    def test_goalposts_moved_keeps_waiting(self):
        cfg = dict(config.load(), poll_sec=1, keep_awake=False, notify=False, resume_grace_sec=0)
        t = time.time()
        d = guard.Decision(resume_at=t + 0.5, reason="x", window="5h")
        readings = iter([Usage("claude", [Window("5h", 99.5, t + 3.5)]), Usage("claude", [Window("5h", 10, t + 18000)])])
        with mock.patch("morphine.guard.get_usage", side_effect=lambda *a, **k: next(readings)):
            started = time.time()
            self.assertEqual(guard.wait_out("claude", d, cfg), "resumed")
        self.assertGreater(time.time() - started, 2.9)


class TestRecheck(Env):
    def test_rechecks_on_interval_until_refilled(self):
        # 31 min scaled down to ~1.2 s; no reset time known, so only interval checks happen.
        cfg = dict(config.load(), poll_sec=1, keep_awake=False, notify=False, recheck_min=0.02)
        t = time.time()
        d = guard.Decision(resume_at=t + 1.2, reason="5h 99% used", window="5h", known_reset=False)
        readings = iter([Usage("claude", [Window("5h", 99.0, None)]),     # still drained
                         Usage("claude", [Window("5h", 3.0, None)])])     # refilled
        calls = []

        def fake_get(*a, **k):
            calls.append(time.time() - t)
            return next(readings)

        with mock.patch("morphine.guard.get_usage", side_effect=fake_get):
            self.assertEqual(guard.wait_out("claude", d, cfg), "resumed")
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1] - calls[0], 1.0)  # second check one interval later

    def test_first_check_waits_full_interval_not_reset_when_disabled(self):
        cfg = dict(config.load(), keep_awake=False, notify=False, recheck_min=31, recheck_at_reset=False)
        t = 1_000_000.0
        self.assertAlmostEqual(guard._next_check(t, t + 60, True, cfg), t + 31 * 60)
        cfg["recheck_at_reset"] = True
        self.assertAlmostEqual(guard._next_check(t, t + 60, True, cfg), t + 60)

    def test_read_error_does_not_resume_before_known_reset(self):
        cfg = dict(config.load(), poll_sec=1, keep_awake=False, notify=False, recheck_min=0.02)
        t = time.time()
        d = guard.Decision(resume_at=t + 2.5, reason="x", window="5h", known_reset=True)
        n = []

        def fake_get(*a, **k):
            n.append(1)
            return Usage("claude", error="offline")

        with mock.patch("morphine.guard.get_usage", side_effect=fake_get):
            started = time.time()
            self.assertEqual(guard.wait_out("claude", d, cfg), "resumed")
        self.assertGreater(time.time() - started, 2.0)  # held until the known reset passed


class TestAdapters(Env):
    def test_codex_session_log(self):
        day = dt.date.today()
        p = Path(os.environ["CODEX_HOME"]) / "sessions" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        p.mkdir(parents=True)
        ts = "2030-01-01T00:00:00.000Z"
        lines = [
            {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 10.0, "window_minutes": 299, "resets_in_seconds": 100},
                "secondary": {"used_percent": 22.0, "window_minutes": 10079, "resets_in_seconds": 1000}}}},
            {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 98.5, "window_minutes": 299, "resets_in_seconds": 600},
                "secondary": {"used_percent": 23.0, "window_minutes": 10079, "resets_at": 1893500000}}}},
        ]
        (p / "rollout-x.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        u = codex.fetch_from_sessions()
        self.assertTrue(u.ok, u.error)
        five, week = u.windows
        self.assertEqual((five.name, five.used_pct), ("5h", 98.5))
        self.assertAlmostEqual(five.resets_at, parse_ts(ts) + 600)
        self.assertEqual((week.name, week.resets_at), ("7d", 1893500000.0))

    def test_codex_app_server_shape(self):
        u = codex.usage_from_rate_limits({
            "primary": {"usedPercent": 50, "windowDurationMins": 300, "resetsAt": 1893456000},
            "secondary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 1893900000},
        }, "app-server")
        self.assertEqual([w.name for w in u.windows], ["5h", "7d"])

    def test_gemini_estimate(self):
        chats = Path(os.environ["GEMINI_HOME"]) / "tmp" / "abc" / "chats"
        chats.mkdir(parents=True)
        nowiso = dt.datetime.now(dt.timezone.utc).isoformat()
        msgs = [{"type": "user", "timestamp": nowiso}] + [{"type": "gemini", "timestamp": nowiso}] * 10 + \
               [{"type": "gemini", "timestamp": "2000-01-01T00:00:00Z"}]
        (chats / "session-1.json").write_text(json.dumps({"messages": msgs}))
        config.set_value("providers.gemini.daily_requests", "100")
        u = gemini.GeminiAdapter().fetch(config.load())
        self.assertEqual(u.windows[0].used_pct, 10.0)
        self.assertTrue(u.windows[0].estimated)

    def test_cache_ttl(self):
        cfg = config.load()
        calls = []

        class A:
            def fetch(self, cfg):
                calls.append(1)
                return Usage("claude", [Window("5h", 1, None)])

        with mock.patch.dict("morphine.adapters.ADAPTERS", {"claude": A()}):
            get_usage("claude", cfg)
            get_usage("claude", cfg)
            get_usage("claude", cfg, max_age=0)
        self.assertEqual(len(calls), 2)

    def test_adapter_crash_never_raises(self):
        class Boom:
            def fetch(self, cfg):
                raise RuntimeError("kaboom")

        with mock.patch.dict("morphine.adapters.ADAPTERS", {"claude": Boom()}):
            u = get_usage("claude", config.load(), max_age=0)
        self.assertFalse(u.ok)


class TestDisplayRule(Env):
    def test_shortest_window_wins(self):
        from morphine.adapters.base import shortest_window
        t = 1_000_000.0
        both = [Window("7d", 99.0, t + 9e5), Window("5h", 10.0, t + 600)]
        self.assertEqual(shortest_window(both, t).name, "5h")
        only_week = [Window("7d", 40.0, t + 9e5)]
        self.assertEqual(shortest_window(only_week, t).name, "7d")
        self.assertIsNone(shortest_window([Window("5h", None, None)], t))

    def test_cached_status_prefers_fresher_local_reading(self):
        from morphine import cli
        from morphine.util import write_json_atomic
        stale = Usage("codex", [Window("5h", 10.0, time.time() + 3600)], source="app-server", fetched_at=time.time() - 300)
        write_json_atomic(config.home() / "cache" / "codex.json", stale.to_dict())
        fresh = Usage("codex", [Window("5h", 55.0, time.time() + 3600)], source="sessions", fetched_at=time.time())
        with mock.patch.object(type(cli.ADAPTERS["codex"]), "fetch_local", return_value=fresh):
            data = cli.collect(config.load(), cached_only=True)
        codex = next(p for p in data["providers"] if p["id"] == "codex")
        self.assertEqual(codex["windows"][0]["used_pct"], 55.0)
        self.assertEqual(codex["display_window"], "5h")


class TestHookCLI(Env):
    def _run(self, args, stdin="", extra_env=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT), **(extra_env or {}))
        return subprocess.run([sys.executable, "-m", "morphine", *args], input=stdin, capture_output=True,
                              text=True, env=env, timeout=30)

    def test_hook_allows_when_unknown(self):
        r = self._run(["hook", "claude"], '{"hook_event_name":"PreToolUse"}')
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = self._run(["hook", "gemini"], "{}")
        self.assertEqual(r.stdout.strip(), "{}")
        r = self._run(["hook", "antigravity"], "{}")
        self.assertEqual(json.loads(r.stdout), {"allow_tool": True})

    def test_grok_running_claude_hook_is_not_judged_by_claude_quota(self):
        sl = {"rate_limits": {"five_hour": {"used_percentage": 99.9, "resets_at": time.time() + 3600}}}
        self._run(["statusline"], json.dumps(sl))
        t = time.time()
        r = self._run(["hook", "claude"], '{"hookEventName":"PreToolUse"}', {"GROK_HOOK_EVENT": "PreToolUse"})
        self.assertLess(time.time() - t, 5)
        self.assertEqual(r.returncode, 0)

    def test_statusline_wrap_and_cache(self):
        sl = {"rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": time.time() + 3600}}}
        r = self._run(["statusline", "--wrap", "echo theirs"], json.dumps(sl))
        self.assertEqual(r.stdout.strip(), "theirs  │  💉 5h 58% left")
        r = self._run(["status", "--json", "--cached"])
        claude = next(p for p in json.loads(r.stdout)["providers"] if p["id"] == "claude")
        self.assertEqual(claude["windows"][0]["used_pct"], 42)


class TestInstall(Env):
    def test_claude_install_idempotent_and_uninstall(self):
        sp = install.claude_settings_path()
        sp.parent.mkdir(parents=True)
        sp.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-line.sh"},
                                  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                                      {"type": "command", "command": "guard.sh"}]}]}}))
        with redirect_stdout(io.StringIO()):
            install.install_claude()
            install.install_claude()
        s = json.loads(sp.read_text())
        cmds = [h["command"] for g in s["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(sum("morphine" in c and "hook claude" in c for c in cmds), 1)
        self.assertIn("guard.sh", cmds)
        self.assertIn("--wrap my-line.sh", s["statusLine"]["command"])
        install.uninstall_all()
        s = json.loads(sp.read_text())
        self.assertEqual(s["statusLine"]["command"], "my-line.sh")
        self.assertNotIn("UserPromptSubmit", s["hooks"])
        self.assertEqual([h["command"] for g in s["hooks"]["PreToolUse"] for h in g["hooks"]], ["guard.sh"])

    def test_codex_block_replaced_not_duplicated(self):
        cp = install.codex_config_path()
        cp.parent.mkdir(parents=True)
        cp.write_text('model = "gpt-5"\n')
        install.install_codex()
        install.install_codex()
        text = cp.read_text()
        self.assertEqual(text.count("# >>> morphine >>>"), 1)
        self.assertTrue(text.startswith('model = "gpt-5"'))
        try:
            import tomllib
        except ImportError:
            tomllib = None
        if tomllib:
            data = tomllib.loads(text)
            self.assertIn("hook codex", data["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        install.uninstall_all()
        self.assertNotIn("morphine", cp.read_text())

    def test_gemini_and_antigravity(self):
        install.install_gemini()
        s = json.loads(install.gemini_settings_path().read_text())
        self.assertEqual(s["hooks"]["BeforeTool"][0]["hooks"][0]["timeout"], install.WAIT_SEC * 1000)
        install.install_antigravity()
        a = json.loads(install.antigravity_hooks_path().read_text())
        self.assertIn("PreToolUse", a["morphine"])


@unittest.skipUnless(sys.platform != "win32", "needs a pty")
class TestWrapper(Env):
    def test_wrapper_detects_limit_and_types_continue(self):
        config.set_value("resume_grace_sec", "0")
        config.set_value("keep_awake", "false")
        config.set_value("notify", "false")
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        t = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "morphine", "run", "--provider", "fake", "--",
             sys.executable, str(ROOT / "tests" / "fake_agent.py")],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=60,
        )
        elapsed = time.time() - t
        self.assertIn("RESUMED-OK", r.stdout, r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0)
        self.assertGreater(elapsed, 1.5)
        log = (Path(os.environ["MORPHINE_HOME"]) / "log.txt").read_text()
        self.assertIn("LOCKOUT fake", log)
        self.assertIn("RESUMED fake", log)


if __name__ == "__main__":
    unittest.main()
