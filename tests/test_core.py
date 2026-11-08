"""Fast checks for the parts that decide what happens to your files.

Standard library only (no pytest or other downloads), and no ffmpeg or
ImageMagick needed. Run from the repository root:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dolphin_easy_convert import jobs, presets, runner  # noqa: E402
from dolphin_easy_convert.jobs import OutputMode  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def touch(self, name: str, data: bytes = b"x") -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_same_stem_in_one_batch_gets_distinct_outputs(self) -> None:
        a, b = self.touch("clip.mkv"), self.touch("clip.mov")
        for mode in OutputMode:
            planned = jobs.plan([a, b], presets.PRESETS_BY_ID["mp4-h264"], mode)
            dests = [j.dest for j in planned]
            self.assertEqual(len(set(dests)), 2, mode)

    def test_existing_and_dangling_names_are_never_targets(self) -> None:
        src = self.touch("photo.png", PNG)
        (self.dir / "converted").mkdir()
        (self.dir / "converted" / "photo.webp").symlink_to("/nonexistent")
        [job] = jobs.plan([src], presets.PRESETS_BY_ID["webp-img"],
                          OutputMode.SUBDIR_CONVERTED)
        self.assertEqual(job.dest.name, "photo-1.webp")

    def test_relative_paths_become_absolute(self) -> None:
        self.touch("-x.mkv")
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            [job] = jobs.plan([Path("./-x.mkv")],
                              presets.PRESETS_BY_ID["mp4-h264"],
                              OutputMode.INPLACE_SUFFIX)
        finally:
            os.chdir(cwd)
        self.assertTrue(str(job.src).startswith("/"))

    def test_disguised_scripts_are_refused(self) -> None:
        for name, data in (("a.png", b"<svg xmlns='x'/>"),
                           ("b.jpg", b"push graphic-context"),
                           ("c.png", b"%!PS-Adobe-3.0"),
                           ("d.gif", b"%PDF-1.7")):
            [job] = jobs.plan([self.touch(name, data)],
                              presets.PRESETS_BY_ID["png"],
                              OutputMode.SUBDIR_CONVERTED)
            self.assertEqual(job.status, "skipped", name)
        [ok] = jobs.plan([self.touch("real.svg", b"<svg/>")],
                         presets.PRESETS_BY_ID["png"],
                         OutputMode.SUBDIR_CONVERTED)
        self.assertEqual(ok.status, "pending")

    def test_finalise_never_overwrites(self) -> None:
        src = self.touch("a.png", PNG)
        [job] = jobs.plan([src], presets.PRESETS_BY_ID["webp-img"],
                          OutputMode.INPLACE_SUFFIX)
        job.prepare()
        job.temp_dest.write_bytes(b"new")
        job.dest.write_bytes(b"appeared meanwhile")
        jobs.finalise(job)
        self.assertEqual((self.dir / "a-conv.webp").read_bytes(),
                         b"appeared meanwhile")
        self.assertEqual(job.dest.read_bytes(), b"new")

    def test_finalise_keeps_original_dates(self) -> None:
        src = self.touch("a.png", PNG)
        os.utime(src, (1_000_000_000, 1_000_000_000))
        [job] = jobs.plan([src], presets.PRESETS_BY_ID["webp-img"],
                          OutputMode.SUBDIR_CONVERTED)
        job.prepare()
        job.temp_dest.write_bytes(b"new")
        jobs.finalise(job)
        self.assertEqual(int(job.dest.stat().st_mtime), 1_000_000_000)

    def test_scratch_files_are_random_and_exclusive(self) -> None:
        src = self.touch("a.png", PNG)
        [job] = jobs.plan([src], presets.PRESETS_BY_ID["png"],
                          OutputMode.INPLACE_SUFFIX)
        first = job.prepare()
        second = job.scratch("x", "png")
        self.assertNotEqual(first, second)
        self.assertTrue(first.name.startswith("."))
        self.assertNotIn(str(os.getpid()), first.name)


class LocalOnlyTests(unittest.TestCase):
    def test_ffmpeg_whitelist_is_local_files_only(self) -> None:
        self.assertEqual(runner.FFMPEG_LOCAL_ONLY,
                         ("-protocol_whitelist", "file"))

    def test_magick_policy_ships_and_is_used(self) -> None:
        policy = runner.MAGICK_POLICY_DIR / "policy.xml"
        self.assertTrue(policy.is_file())
        text = policy.read_text()
        for needle in ("HTTP", "HTTPS", "URL", "https:decode", "MSL", "@*"):
            self.assertIn(needle, text)
        env = runner.magick_env()
        self.assertTrue(env["MAGICK_CONFIGURE_PATH"].startswith(
            str(runner.MAGICK_POLICY_DIR)))

    def test_system_tools_win_over_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "sh"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(0o755)
            old = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{d}:{old}"
            try:
                self.assertEqual(presets.tool_path("sh"), "/usr/bin/sh")
            finally:
                os.environ["PATH"] = old


class AnimationTests(unittest.TestCase):
    def setUp(self) -> None:
        from dolphin_easy_convert.animate import AnimSettings
        self.S = AnimSettings

    def test_webp_and_gif_arguments(self) -> None:
        webp = self.S("webp", 5, 3, 480, 15, 80, False, 256, "none").preset()
        self.assertEqual(webp.ext, "webp")
        self.assertIn("libwebp_anim", webp.args)
        self.assertEqual(webp.input_args, ("-ss", "5.000", "-t", "3.000"))
        gif = self.S("gif", 0, 0, 0, 10, 0, False, 64, "none").preset()
        self.assertEqual(gif.input_args, ())
        graph = gif.args[gif.args.index("-vf") + 1]
        self.assertIn("max_colors=64", graph)
        self.assertNotIn("scale", graph)  # original size

    def test_progress_uses_trimmed_length(self) -> None:
        self.assertEqual(runner._clip_length(("-ss", "5", "-t", "3"), 20.0), 3.0)
        self.assertEqual(runner._clip_length(("-ss", "18",), 20.0), 2.0)
        self.assertIsNone(runner._clip_length(("-ss", "30",), 20.0))


class GpuTests(unittest.TestCase):
    def test_gpu_variant_swaps_encoder_and_keeps_quality(self) -> None:
        old = presets.RENDER_NODE, presets.gpu_works
        presets.RENDER_NODE = Path("/dev/null")
        presets.gpu_works = lambda _enc: True
        try:
            if "h264_vaapi" not in presets.ffmpeg_encoders():
                self.skipTest("ffmpeg here has no h264_vaapi")
            gpu = presets.gpu_variant(presets.PRESETS_BY_ID["mp4-h264"])
        finally:
            presets.RENDER_NODE, presets.gpu_works = old
        self.assertIn("h264_vaapi", gpu.args)
        self.assertNotIn("libx264", gpu.args)
        self.assertEqual(gpu.args[gpu.args.index("-qp") + 1], "20")
        self.assertIn("-vaapi_device", gpu.input_args)

    def test_no_gpu_variant_for_other_scales(self) -> None:
        self.assertIsNone(presets.gpu_encoder_name(
            presets.PRESETS_BY_ID["mp4-av1"]))


class InstanceTests(unittest.TestCase):
    def test_requests_are_validated(self) -> None:
        try:
            from dolphin_easy_convert import instance
        except ImportError:
            self.skipTest("PyQt6 not installed")
        with tempfile.NamedTemporaryFile(suffix=".mp4") as real:
            good = instance.make_request("mp3", False, None, [Path(real.name)])
            self.assertIsNotNone(instance.validate(json_line(good)))
        for bad in ({**good, "preset": "rm -rf"}, {**good, "files": ["rel.mp4"]},
                    {**good, "files": ["/nonexistent/x.mp4"]},
                    {**good, "animate": "exe"}, {**good, "v": 99}):
            self.assertIsNone(instance.validate(json_line(bad)), bad)
        self.assertIsNone(instance.validate(b"\xff not json"))


def json_line(obj) -> bytes:
    import json
    return json.dumps(obj).encode()


class ErrorTests(unittest.TestCase):
    def test_common_errors_are_explained(self) -> None:
        self.assertIn("disk is full",
                      runner.explain("write: No space left on device").summary)
        err = runner.explain("something nobody anticipated")
        self.assertEqual(err.summary, "something nobody anticipated")


if __name__ == "__main__":
    unittest.main()
