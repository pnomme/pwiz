# PWIZ - EXIF/XMP Metadata Processor

PWIZ kopierer metadata fra XMP-sidecar til bilder, og kan i tillegg geotagge bilder uten GPS ved hjelp av Google Timeline JSON.

## Features

- Leser XMP-sidecar og skriver metadata til bilde via `exiftool`
- Støtter GPS, `CreateDate` og `ImageDescription`
- Kan slette sidecar etter behandling (med valgfri backup)
- Kan lese Google Timeline-filer og skrive GPS til bilder som mangler GPS

## Prerequisites

- Python 3
- [ExifTool](https://exiftool.org/)

## Usage

```sh
python3 pwiz.py <root_directory> [options]
```

Viktige valg:

- `--gps`, `--date`, `--description`: begrens hvilke felter som behandles
- `--timeline <path>`: Google Timeline JSON-fil eller mappe med JSON-filer
- `--timeline-max-gap <minutes>`: maks tidsavvik mellom bilde og Timeline-punkt (standard `30`)
- `--force`: overskriv eksisterende GPS (standard er å beholde GPS som finnes)
- `--dryrun`: vis hva som ville blitt skrevet, uten å endre filer
- `--backup <folder>` eller `--nobackup`: påkrevd sikkerhetsvalg

## Example

Skriv GPS fra Google Timeline til bilder uten GPS:

```sh
python3 pwiz.py /path/to/photos \
  --timeline /path/to/google-timeline \
  --gps \
  --dryrun \
  --nobackup
```

Kjør uten `--dryrun` når resultatet ser riktig ut.

## Credits
Peter Nomme 2024-2026, anycloud as
