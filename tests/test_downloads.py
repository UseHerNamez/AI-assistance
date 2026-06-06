"""Tests for download watcher filename filtering."""

from __future__ import annotations

import unittest

from quest_assistant.events.sources.downloads import _is_finished_download_filename


class DownloadFilenameTests(unittest.TestCase):
    def test_partial_suffixes_skipped(self) -> None:
        self.assertFalse(_is_finished_download_filename("movie.crdownload"))
        self.assertFalse(_is_finished_download_filename("setup.part"))
        self.assertFalse(_is_finished_download_filename("data.tmp"))
        self.assertFalse(_is_finished_download_filename("file.download"))

    def test_normal_files_allowed(self) -> None:
        self.assertTrue(_is_finished_download_filename("report.pdf"))
        self.assertTrue(_is_finished_download_filename("photo.jpg"))


if __name__ == "__main__":
    unittest.main()
