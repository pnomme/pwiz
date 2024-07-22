import subprocess
import json
import os
import argparse
import re
import shutil
from datetime import datetime

def dms_to_decimal(degrees, minutes, seconds, direction):
    """Convert DMS (Degrees, Minutes, Seconds) format to decimal degrees."""
    decimal = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    if direction in ['S', 'W']:
        decimal = -decimal
    return decimal

def parse_gps_data(gps_string):
    """Parse GPS string into degrees, minutes, seconds."""
    dms_pattern = re.compile(
        r'(?P<degrees>\d+)[^\d]*(?P<minutes>\d+)[\'"]?\s+(?P<seconds>\d+\.?\d*)["]?\s*(?P<direction>[NSEW])'
    )
    
    match = dms_pattern.search(gps_string)
    if match:
        degrees = match.group('degrees')
        minutes = match.group('minutes')
        seconds = match.group('seconds')
        direction = match.group('direction')
        return dms_to_decimal(degrees, minutes, seconds, direction)
    else:
        print(f"Unable to parse GPS data: {gps_string}")
        return None

def normalize_datetime(dt_string):
    """Normalize datetime string to a common format."""
    try:
        # Remove milliseconds and timezone info if present
        dt_string = dt_string.split('.')[0]
        # Parse datetime assuming it's in the format YYYY:MM:DD HH:MM:SS
        dt = datetime.strptime(dt_string, '%Y:%m:%d %H:%M:%S')
        # Return in ISO 8601 format (without timezone for comparison)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        print(f"Error parsing datetime string: {dt_string}. Error: {e}")
        return None

def parse_gps_and_date_data(xmp_data, parse_gps, parse_date, parse_description):
    """Extract GPS coordinates and CreationDate from sidecar XMP file."""
    gps_data = {}
    
    if parse_gps:
        latitude_str = xmp_data.get('GPSLatitude')
        longitude_str = xmp_data.get('GPSLongitude')
        latitude_ref = xmp_data.get('GPSLatitudeRef', 'N')
        longitude_ref = xmp_data.get('GPSLongitudeRef', 'E')
        
        if latitude_str and longitude_str:
            latitude = parse_gps_data(latitude_str)
            longitude = parse_gps_data(longitude_str)
            if latitude is not None and longitude is not None:
                gps_data['latitude'] = latitude
                gps_data['longitude'] = longitude
                gps_data['latitude_ref'] = latitude_ref[0]
                gps_data['longitude_ref'] = longitude_ref[0]
            else:
                print(f"Error converting GPS data.")
    
    if parse_date:
        creation_date = xmp_data.get('CreationDate')
        if creation_date:
            gps_data['creation_date'] = normalize_datetime(creation_date)

    if parse_description:
        gps_data['description'] = xmp_data.get('ImageDescription')

    return gps_data if gps_data else None

def read_gps_and_creation_date_from_sidecar(xmp_path, parse_gps, parse_date, parse_description):
    """Extract GPS coordinates and CreationDate from sidecar XMP file using ExifTool."""
    try:
        result = subprocess.run(
            ['exiftool', '-j', xmp_path],
            capture_output=True,
            text=True,
            check=True
        )
        
        metadata = json.loads(result.stdout)
        if metadata:
            xmp_data = metadata[0]
            description = xmp_data.get('ImageDescription')
            return parse_gps_and_date_data(xmp_data, parse_gps, parse_date, description)
        else:
            print(f"No XMP metadata found in {xmp_path}")
            return None

    except subprocess.CalledProcessError as e:
        print(f"Error reading metadata from {xmp_path}: {e}")
        return None

def read_existing_exif_data(image_path):
    """Read existing EXIF data from image file using ExifTool."""
    try:
        result = subprocess.run(
            ['exiftool', '-j', image_path],
            capture_output=True,
            text=True,
            check=True
        )
        
        metadata = json.loads(result.stdout)
        if metadata:
            exif_data = metadata[0]
            return exif_data
        else:
            return None

    except subprocess.CalledProcessError as e:
        print(f"Error reading metadata from {image_path}: {e}")
        return None

def extract_gps_value(gps_string):
    """Extract GPS value from EXIF format."""
    parts = re.split(r'[^\d.]+', gps_string)
    try:
        degrees = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        direction = gps_string[-1]  # Last character is the direction (N/S/E/W)
        return dms_to_decimal(degrees, minutes, seconds, direction)
    except (IndexError, ValueError) as e:
        print(f"Error extracting GPS value from string '{gps_string}': {e}")
        return None

def convert_exif_gps_to_decimal(gps_latitude, gps_latitude_ref, gps_longitude, gps_longitude_ref):
    """Convert EXIF GPS coordinates to decimal degrees for comparison."""
    latitude = extract_gps_value(gps_latitude)
    longitude = extract_gps_value(gps_longitude)
    if latitude is not None and longitude is not None:
        return latitude, longitude
    else:
        print(f"Error converting EXIF GPS data.")
        return None, None


def write_gps_and_creation_date_and_description_to_exif(image_path, gps_data, dry_run, keep_original):
    """Write GPS coordinates, CreationDate, and Description to EXIF metadata using ExifTool."""

    print(f"{os.path.basename(image_path)}:")
    if not gps_data:
        print("No GPS data, CreationDate, or Description to write.")
        return
    
    exif_data = read_existing_exif_data(image_path)
    if not exif_data:
        print(f"Could not read existing EXIF data for {image_path}.")
        return
    
    data_written = []
    if keep_original:
        command = ['exiftool']  # option added to avoid creating _original
    else:
        command = ['exiftool', '-overwrite_original']  # option added to avoid creating _original

    # Compare GPS data
    if 'latitude' in gps_data and 'longitude' in gps_data:
        existing_latitude = exif_data.get('GPSLatitude')
        existing_longitude = exif_data.get('GPSLongitude')
        existing_latitude_ref = exif_data.get('GPSLatitudeRef', 'N')
        existing_longitude_ref = exif_data.get('GPSLongitudeRef', 'E')

        if existing_latitude and existing_longitude:
            exif_latitude, exif_longitude = convert_exif_gps_to_decimal(
                existing_latitude,
                existing_latitude_ref,
                existing_longitude,
                existing_longitude_ref
            )

            print(f" - Existing EXIF GPS: lat={exif_latitude}, long={exif_longitude}")

            if (round(exif_latitude, 6) == round(gps_data['latitude'], 6) and
                round(exif_longitude, 6) == round(gps_data['longitude'], 6)):
                print(f" - GPS data not changed, Skipped.")
            else:
                print(f" - New GPS: lat={gps_data['latitude']}, long={gps_data['longitude']}")
                command.extend([
                    f"-GPSLatitude={gps_data['latitude']}",
                    f"-GPSLongitude={gps_data['longitude']}",
                    f"-GPSLatitudeRef={gps_data['latitude_ref']}",
                    f"-GPSLongitudeRef={gps_data['longitude_ref']}",
                ])
                data_written.append("GPS")
        else:
            print(f" - New GPS: lat={gps_data['latitude']}, long={gps_data['longitude']}")
            command.extend([
                f"-GPSLatitude={gps_data['latitude']}",
                f"-GPSLongitude={gps_data['longitude']}",
                f"-GPSLatitudeRef={gps_data['latitude_ref']}",
                f"-GPSLongitudeRef={gps_data['longitude_ref']}",
            ])
            data_written.append("GPS")

    # Compare CreationDate
    if 'creation_date' in gps_data:
        existing_creation_date = exif_data.get('CreateDate')

        if existing_creation_date:
            normalized_existing_date = normalize_datetime(existing_creation_date)
            print(f" - Existing EXIF CreationDate: {normalized_existing_date}")

            if normalized_existing_date == gps_data['creation_date']:
                print(f" - CreationDate not changed, Skipped.")
            else:
                print(f" - New CreationDate: {gps_data['creation_date']}")
                command.append(f"-CreateDate={gps_data['creation_date']}")
                data_written.append("CreationDate")
        else:
            print(f" - New CreationDate: {gps_data['creation_date']}")
            command.append(f"-CreateDate={gps_data['creation_date']}")
            data_written.append("CreationDate")

    # Handle Description
#    print(gps_data)
    if 'description' in gps_data:
        existing_description = exif_data.get('ImageDescription')
        if existing_description:
            print(f" - Existing EXIF Description: {existing_description}")

            if existing_description == gps_data['description']:
                print(f" - Description not changed, Skipped.")
            else:
                print(f" - New Description: {gps_data['description']}")
                command.append(f"-ImageDescription={gps_data['description']}")
                data_written.append("Description")
        else:
            print(f" - New Description: {gps_data['description']}")
            command.append(f"-ImageDescription={gps_data['description']}")
            data_written.append("Description")

    if data_written:
        if dry_run:
            print(f"Dry run: {', '.join(data_written)} data would be written to {os.path.basename(image_path)}")
            return ', '.join(data_written)
        else:
            # Avoid creating a _original file
            command.append("-overwrite_original")
            command.append(image_path)
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"=> {', '.join(data_written)} data written to {os.path.basename(image_path)}")
                return ', '.join(data_written)
            else:
                print(f"Error writing metadata to {image_path}: {result.stderr}")
                return None

def find_image_file(base_filename, dirpath):
    """Find image files with the same base name, prioritizing no extension first."""
    possible_extensions = ['', '.jpg', '.jpeg', '.png', '.tif', '.tiff']  # No extension first
    
    no_ext_path = os.path.join(dirpath, base_filename)
    if os.path.isfile(no_ext_path):
        return no_ext_path
    
    for ext in possible_extensions[1:]:
        image_path = os.path.join(dirpath, base_filename + ext)
        if os.path.isfile(image_path):
            return image_path
            
    return None

def ensure_unique_filename(filepath):
    """Ensure the filepath is unique by appending a number to the filename if it already exists."""
    if not os.path.exists(filepath):
        return filepath
    
    base, ext = os.path.splitext(filepath)
    i = 1
    while os.path.exists(filepath):
        filepath = f"{base}_{i}{ext}"
        i += 1
    return filepath
    
def process_images_and_sidecars(root_directory, delete_xmp, backup_directory, parse_gps, parse_date, dry_run, parse_description, keep_original):
    """Process images and sidecar XMP files."""
    total_files = 0
    total_skipped = 0
    total_written_gps = 0
    total_written_date = 0
    total_written_description = 0
    
    for dirpath, _, files in os.walk(root_directory):
        for file in files:
            if file.lower().endswith('.xmp'):
                xmp_path = os.path.join(dirpath, file)
                base_filename = os.path.splitext(file)[0]
                
                image_path = find_image_file(base_filename, dirpath)
                if image_path:
                    gps_data = read_gps_and_creation_date_from_sidecar(xmp_path, parse_gps, parse_date, parse_description)
#                    description_data = read_description_from_sidecar(xmp_path, parse_description)
                    
                    if gps_data: #or description_data:
                        total_files += 1
                        result = write_gps_and_creation_date_and_description_to_exif(image_path, gps_data, dry_run, keep_original)
                        if result == 'skipped':
                            total_skipped += 1
                        if result and 'GPS' in result:
                            total_written_gps += 1
                        if result and 'CreationDate' in result:
                            total_written_date += 1
                        if result and 'Description' in result:
                            total_written_description += 1
                    
                        if delete_xmp and not dry_run:
                            if backup_directory:
                                # Create the corresponding directory structure in the backup location
                                backup_path = os.path.join(backup_directory, os.path.relpath(xmp_path, root_directory))
                                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                                backup_path = ensure_unique_filename(backup_path)
                                shutil.copy2(xmp_path, backup_path)
                                print(f"Copied sidecar file to backup: {backup_path}")
                            try:
                                os.remove(xmp_path)
                                print(f"Deleted sidecar file: {xmp_path}")
                            except OSError as e:
                                print(f"Error deleting sidecar file {xmp_path}: {e}")
                else:
                    print(f"Image file corresponding to sidecar {xmp_path} not found.")
    
    # Print summary
    print("\nSummary:")
    print(f"Total files processed: {total_files}")
    print(f"Total files skipped: {total_skipped}")
    print(f"Total files written (GPS): {total_written_gps}")
    print(f"Total files written (CreationDate): {total_written_date}")
    print(f"Total files written (Description): {total_written_description}")


def main():
    parser = argparse.ArgumentParser(description="Process images and sidecar XMP files to transfer metadata.")
    parser.add_argument('root_directory', type=str, help="Root directory to process.")
    parser.add_argument('--delete', action='store_true', help="Delete XMP sidecar files after processing.")
    parser.add_argument('--backup', type=str, help="Backup location for XMP sidecar files.")
    parser.add_argument('--keep', action='store_true', help="Keep a copy of the file as _original.")
    parser.add_argument('--gps', action='store_true', help="Only parse GPS data from sidecar files.")
    parser.add_argument('--date', action='store_true', help="Only parse CreationDate from sidecar files.")
    parser.add_argument('--description', action='store_true', help="Only parse Description from sidecar files.")
    parser.add_argument('--dryrun', action='store_true', help="Run the script without making any changes.")

    args = parser.parse_args()
    
    # If no --GPS or --Date is specified, default to both
    parse_gps = args.gps or (not args.date and not args.description)
    parse_date = args.date or (not args.gps and not args.description)
    parse_description = args.description or (not args.gps and not args.date)

    process_images_and_sidecars(args.root_directory, args.delete, args.backup, parse_gps, parse_date, args.dryrun, parse_description, args.keep)

if __name__ == "__main__":
    main()
