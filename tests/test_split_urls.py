import tempfile
import unittest
from pathlib import Path

from local_video_site.downloader.entrypoints.split_urls import chunk_urls, read_unique_urls, split_url_file


class SplitUrlTests(unittest.TestCase):
    def test_duplicate_filter_keeps_first_occurrence_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "urls.txt"
            path.write_text(
                "\ufeffhttps://example.com/a\n"
                "\n"
                " https://example.com/b \n"
                "https://example.com/a\n"
                "https://example.com/c\n"
                "https://example.com/b\n",
                encoding="utf-8",
            )
            urls, line_count, duplicate_count = read_unique_urls(path)

        self.assertEqual(
            urls,
            [
                "https://example.com/a",
                "https://example.com/b",
                "https://example.com/c",
            ],
        )
        self.assertEqual(line_count, 5)
        self.assertEqual(duplicate_count, 2)

    def test_urls_are_split_into_default_chunks_of_fifteen(self):
        urls = ["https://example.com/{}".format(index) for index in range(31)]
        chunks = chunk_urls(urls)

        self.assertEqual([len(chunk) for chunk in chunks], [15, 15, 1])
        self.assertEqual(chunks[0][0], urls[0])
        self.assertEqual(chunks[-1][-1], urls[-1])

    def test_split_files_keep_original_txt_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            original = directory / "input.txt"
            original_content = "\n".join(
                "https://example.com/{}".format(index)
                for index in [*range(17), 0, 1, 2]
            ) + "\n"
            original.write_text(original_content, encoding="utf-8")

            result = split_url_file(original)

            self.assertEqual(original.read_text(encoding="utf-8"), original_content)
            self.assertEqual(
                [path.name for path in result.output_paths],
                ["input-1.txt", "input-2.txt"],
            )
            self.assertEqual(len(result.output_paths[0].read_text().splitlines()), 15)
            self.assertEqual(len(result.output_paths[1].read_text().splitlines()), 2)
            self.assertEqual(result.duplicate_count, 3)

    def test_existing_output_requires_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            original = directory / "urls.txt"
            original.write_text("https://example.com/a\n", encoding="utf-8")
            output = directory / "urls-1.txt"
            output.write_text("old\n", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "--force"):
                split_url_file(original)

            split_url_file(original, force=True)
            self.assertEqual(output.read_text(encoding="utf-8"), "https://example.com/a\n")

    def test_empty_input_creates_one_empty_chunk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = Path(temp_dir) / "empty.txt"
            original.write_text("\n\n", encoding="utf-8")
            result = split_url_file(original)

            self.assertEqual(len(result.output_paths), 1)
            self.assertEqual(result.output_paths[0].read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
