#!/usr/bin/env python3
"""
Upload files to Immich.

This script is used because the immich app isn't reliable for uploading files, and I don't want to
manually upload files via the web interface (and leave that interface open in Chrome).

Instead, this cli script can be run as a periodic cronjob.

See also the organize.py script for organizing files into directories prior to this script being 
executed.

This script is referenced in bash_aliases (but not in the github copy of it).

Version 1.0
Date: 2024-09-20
Status: Working

Example:
    >>> python progressive.py
    >>> python progressive.py -d /mnt/i/Phone
    # bash_aliases defines `upload` to run this script for the current dir
    >>> upload
"""
from __future__ import annotations
import logging
import os
import sys
import time

# Add the root directory of the project to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from typing import Collection, Generator
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv
import argparse
from tqdm import tqdm
from scripts import setup_logging
from scripts.lib.file_manager import FileManager
from scripts.thumbnails.upload.exceptions import AuthenticationError
from scripts.thumbnails.upload.interface import ImmichInterface
from scripts.thumbnails.upload.status import Status

logger = setup_logging()

class ImmichProgressiveUploader(ImmichInterface):

    def _upload_file(self, file: Path) -> bool:
        """
        Upload a file to Immich. To use this, call upload_file_threadsafe, which wraps this method.

        Args:
            file (Path): The file to upload.

        Returns:
            bool: True on success (i.e. the file was uploaded successfully), False on error.
        """
        if self.should_ignore_file(file):
            logger.debug('Ignoring %s', file)
            return True

        if self.check_dry_run('running immich upload'):
            return True
        
        command = ["immich", "upload", file.as_posix()]
        try:
            result = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            output = result.stdout + result.stderr

            # Analyze the output
            if "All assets were already uploaded" in output:
                logger.debug("%s already uploaded.", file)
                return True
            if "Unsupported file type" in output:
                logger.debug("Unsupported file type: %s", file)
                return False
            if "Successfully uploaded" in output:
                logger.debug("Uploaded %s successfully.", file)
                return True

            logger.info('Unknown output: %s', output)
            logger.info('By default, marking file %s uploaded successfully.', file)
            return True
        except subprocess.CalledProcessError as e:
            output = e.stdout + e.stderr
            logger.error(f"Failed to upload {file}: {output}")

        return False

    def upload_file_threadsafe(self, file: Path, status: Status) -> bool:
        """
        Upload a file to Immich in a thread-safe manner.

        Args:
            file (Path): The file to upload.
            status (Status): An instance of the Status class.

        Returns:
            bool: True if the file was uploaded successfully, False otherwise.    
        """
        filename = file.name

        success = self._upload_file(file)

        status.update_status(filename, success)

        return success

    def get_directories(self, directory: Path, recursive: bool = True) -> list[Path]:
        """
        Get a list of directories to upload.

        Args:
            directory (Path): The directory to search.
            recursive (bool): Whether to search recursively.

        Returns:
            list[Path]: A list of directories to upload.
        """
        if not recursive:
            return [directory]

        logger.info('Searching %s for directories.', directory.absolute())

        result = []
        for dirpath, dirnames, _ in os.walk(directory):
            # Remove hidden directories from dirnames so os.walk doesn't traverse into them
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            # Skip the directory if it's hidden
            if Path(dirpath).name.startswith('.'):
                continue

            result.append(Path(dirpath))

        return result

    def upload(self, directory: Path | None = None, recursive: bool = True, max_threads: int = 4):
        """
        Upload files to Immich.

        Args:
            directory (Path): The directory to upload.
            recursive (bool): Whether to upload recursively.
            max_threads (int): The maximum number of threads for concurrent uploads.
        """
        if not self._authenticated:
            self.authenticate()

        directory = directory or self.directory
        directories = self.get_directories(directory, recursive=recursive)

        logger.info("Uploading files from %d directories.", len(directories))

        for directory in tqdm(directories, desc="Directories"):
            status = Status(directory=directory)
            status.load()

            # Check if the directory has changed since the last processed time
            if not status.directory_changed():
                logger.debug(f"Skipping directory {directory} as it has not changed since last processed.")
                continue

            try:
                files_to_upload = directory.iterdir()

                with ThreadPoolExecutor(max_workers=max_threads) as executor:
                    futures = []
                    for file in files_to_upload:
                        future = executor.submit(self.upload_file_threadsafe, file, status)
                        futures.append(future)

                    for future in tqdm(
                        as_completed(futures), 
                        total=len(futures), 
                        unit='files', 
                        leave=False,
                        desc=f"Uploading {directory.name}"
                    ):
                        # Re-raise any exceptions
                        future.result()
            finally:
                # Update last_processed_time and save the status file
                status.last_processed_time = time.time()
                status.save()

def main():
    """
    Called when the script is run from the command line. Parses arguments and uploads files to Immich.
    """
    try:
        load_dotenv()

        url = os.getenv("IMMICH_URL")
        api_key = os.getenv("IMMICH_API_KEY")
        thumbnails_dir = os.getenv("CLOUD_THUMBNAILS_DIR", '.')

        parser = argparse.ArgumentParser(description="Upload files to Immich.")
        parser.add_argument("--url", help="Immich URL", default=url)
        parser.add_argument("--api-key", help="Immich API key", default=api_key)
        parser.add_argument("--thumbnails-dir", '-d', help="Cloud thumbnails directory", default=thumbnails_dir)
        parser.add_argument("--ignore-extensions", "-e", help="Ignore files with these extensions", nargs='+')
        parser.add_argument('--ignore-paths', '-i', help="Ignore files with these paths", nargs='+')
        parser.add_argument('--max-threads', '-t', type=int, default=4, help="Maximum number of threads for concurrent uploads")
        parser.add_argument('--verbose', '-v', action='store_true', help="Verbose output")
        args = parser.parse_args()
        
        if args.verbose:
            logger.setLevel(logging.DEBUG)

        if not args.url or not args.api_key or not args.thumbnails_dir:
            logger.error("IMMICH_URL, IMMICH_API_KEY, and CLOUD_THUMBNAILS_DIR must be set.")
            sys.exit(1)
            
        immich = ImmichProgressiveUploader(
            url=args.url,
            api_key=args.api_key,
            directory=Path(args.thumbnails_dir),
            ignore_extensions=args.ignore_extensions,
            ignore_paths=args.ignore_paths
        )
        immich.upload(max_threads=args.max_threads)

    except AuthenticationError:
        logger.error("Authentication failed. Check your API key and URL.")
    except KeyboardInterrupt:
        logger.info("Upload cancelled by user.")

if __name__ == "__main__":
    main()