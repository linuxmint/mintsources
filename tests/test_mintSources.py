import sys, os

tests_path = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, tests_path + "/../usr/lib/linuxmint/mintSources/")

from pathlib import Path
import unittest

import repolib

from mintSources import repo_key_path


class MintSources(unittest.TestCase):
    def test_repo_key_path(self):
        repolib.util.SOURCES_DIR = Path(tests_path, "testdata", "sources.list.d")
        repolib.load_all_sources()

        nemo_uri = "https://nemo.linuxmint.com"
        xviewer_uri = "https://xviewer.linuxmint.com"
        xviewerdev_uri = "https://xviewer-dev.linuxmint.com"
        sticky_uri = "https://sticky.linuxmint.com"
        hypnotix_uri = "https://hypnotix.linuxmint.com"
        pix_uri = "https://pix.linuxmint.com"

        # Deb822 source format.
        self.assertEqual(repo_key_path(nemo_uri), "/usr/share/keyrings/nemo.gpg")
        # Legacy source format.
        self.assertEqual(repo_key_path(xviewer_uri), "/usr/share/keyrings/xviewer.gpg")
        # Input URI with trailing slash.
        self.assertEqual(repo_key_path(nemo_uri + "/"), "/usr/share/keyrings/nemo.gpg")
        # Source repository URI with trailing slash.
        self.assertEqual(
            repo_key_path(xviewerdev_uri), "/usr/share/keyrings/xviewer-dev.gpg"
        )
        # Not signed-by source.
        self.assertEqual(repo_key_path(sticky_uri), None)
        # Signed but disabled Deb822 source format.
        self.assertEqual(repo_key_path(pix_uri), None)
        # Signed but disabled legacy source format.
        self.assertEqual(repo_key_path(hypnotix_uri), None)


if __name__ == "__main__":
    unittest.main()
