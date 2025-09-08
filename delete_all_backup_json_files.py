#!/usr/bin/env python3
"""
A simple utility script to delete all automatically created backup JSON files from `.codex`.
The CodexMaster bot supports running this script at fixed intervals as a cron job.
"""

import os

DOT_CODEX_DIR = os.path.expanduser("~/.codex")
CODEX_MASTER_DIR = os.path.join(DOT_CODEX_DIR, "codex-master")


def main():
    for instance in os.listdir(CODEX_MASTER_DIR):
        inst_dir = os.path.join(CODEX_MASTER_DIR, instance)
        sess_dir = os.path.join(inst_dir, "sessions")
        for sess_file in os.listdir(sess_dir):
            sess_path = os.path.join(sess_dir, sess_file)
            os.remove(sess_path)


if __name__ == "__main__":
    main()

