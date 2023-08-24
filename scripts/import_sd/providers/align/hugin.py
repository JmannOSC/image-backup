"""

	Metadata:

		File: hugin.py
		Project: imageinn
		Created Date: 23 Aug 2023
		Author: Jess Mann
		Email: jess.a.mann@gmail.com

		-----

		Last Modified: Wed Aug 23 2023
		Modified By: Jess Mann

		-----

		Copyright (c) 2023 Jess Mann
"""
from __future__ import annotations
import os
import subprocess
import logging
import re
from tqdm import tqdm

from scripts.lib.path import FilePath, DirPath
from scripts.import_sd.providers.align.base import AlignmentProvider
from scripts.import_sd.photo import Photo
from scripts.import_sd.photostack import PhotoStack

logger = logging.getLogger(__name__)

class HuginProvider(AlignmentProvider):
	"""
	Align images using Hugin's align_image_stack command.
	"""
	aligned_path : DirPath

	def __init__(self, aligned_path : DirPath) -> None:
		super().__init__()
		self.aligned_path = aligned_path

	def next(self, photos: list[Photo] | PhotoStack) -> list[Photo]:
		"""
		Align a single bracket of photos

		Args:
			photo (list[Photo]): The photos to align.

		Returns:
			list[Photo]:
				The aligned photos.
				If ANY of the photos cannot be aligned, an empty list will be returned.
		"""
		if isinstance(photos, PhotoStack):
			photos = photos.get_photos()

		# Ensure aligned_path exists, and create it if not
		self.aligned_path.ensure_exists()
		logger.debug('Aligned path is %s -> exists: %s', self.aligned_path, self.aligned_path.exists())

		aligned_photos : list[Photo] = []
		expected_photos: dict[Photo, FilePath] = {}
		idx : int
		photo : Photo
		for idx, photo in enumerate(photos):
			expected_photos[photo] = self.aligned_path.file(f'aligned_tmp_{idx:04}.tif')

		try:
			# TODO conflicts
			# Create the command
			command = ['align_image_stack', '-a', self.aligned_path.file('aligned_tmp_').path, '-m', '-v', '-C', '-c', '25', '-p', 'hugin.out', '-t', '3']
			for photo in photos:
				command.append(photo.path)
			_output, _error = self.subprocess(command)

			# Ensure the right number of photos were created
			for photo, output_photo in expected_photos.items():
				if not output_photo.exists():
					logger.error('Could not find file after alignment: %s -> %s', photo, output_photo)
					logger.error('OUTPUT: %s', _output)
					logger.error('ERROR: %s', _error)
					import sys
					sys.exit(1)
					return []

			# Add exif data to the aligned photos
			for photo, aligned in tqdm(expected_photos.items(), desc="Aligning Images...", ncols=100):
				# Copy EXIF data using ExifTool
				logger.debug('Copying exif data from %s to %s', photo, aligned)
				self.subprocess(['exiftool', '-TagsFromFile', photo.path, '-all', aligned.path])

				# Create a new file named {photo.filename}_aligned.{ext}
				final_path = photo.change_extension('tif', '_aligned')
				final_path = aligned.rename(final_path.filename)
				final_path = Photo(final_path)

				# Add the photo to the list
				aligned_photos.append(final_path)

		except subprocess.CalledProcessError as e:
			logger.error('Could not align images -> %s', e)
			return []
		
		finally:
			# Clean up aligned photos we created
			for aligned_photo in aligned_photos:
				logger.critical('Deleting %s. Exists %s', aligned_photo, aligned_photo.exists())
				aligned_photo.delete()

		return aligned_photos