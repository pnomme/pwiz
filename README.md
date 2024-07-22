# PWIZ - EXIF/XMP Metadata Processor

This script processes XMP sidecar files and updates the EXIF metadata in the corresponding images.

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Examples](#examples)
- [Contributing](#contributing)
- [License](#license)

## Introduction

The EXIF/XMP Metadata Processor is a tool designed to extract metadata from images. It supports flexible command-line options, including the ability to specify a backup location for original images and XMP sidecar files.

## Features

- Extract EXIF metadata from images
- Extract and handle XMP sidecar files
- Specify backup locations for XMP files
- Flexible command-line options for metadata processing

## Installation

### Prerequisites

- Python 3.x
- `exiftool` (required for metadata extraction)

### Install `exiftool`

Download and install `exiftool` from [ExifTool](https://exiftool.org/).

### Clone the Repository

```sh
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
