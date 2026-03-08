#PWIZ, Peter N 2024

import subprocess
import json
import os
import argparse
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from bisect import bisect_left

verbose = False

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.heic', '.heif', '.webp', '.cr3'}
TIMELINE_TIMESTAMP_KEYS = {
    'timestamp',
    'timestampms',
    'starttime',
    'endtime',
    'time',
    'timemillis',
    'recordedtime',
    'devicetimestamp',
    'derivedtimestamp',
    'additionaltimestamp',
    'createtime',
    'deliverytime',
}

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

def parse_timestamp_value(value):
    """Parse various Google Timeline timestamp formats into UTC datetime."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        epoch_value = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None

        # ISO 8601 variant often used in Timeline exports.
        if '-' in text and 'T' in text:
            text = text.replace('Z', '+00:00')
            try:
                dt = datetime.fromisoformat(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                return None

        if text.isdigit():
            epoch_value = float(text)
        else:
            return None

    # Heuristic for epoch unit.
    if epoch_value > 1e14:
        epoch_seconds = epoch_value / 1_000_000
    elif epoch_value > 1e11:
        epoch_seconds = epoch_value / 1_000
    else:
        epoch_seconds = epoch_value

    try:
        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None

def parse_lat_lng_value(value):
    """Parse Google Timeline coordinate payloads."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None

    if not isinstance(value, str):
        return None

    text = value.strip()
    if text.lower().startswith('geo:'):
        text = text[4:]

    parts = [part.strip() for part in text.split(',')]
    if len(parts) < 2:
        return None

    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None

def extract_lat_lon_from_node(node):
    """Extract latitude/longitude from one Timeline node."""
    if not isinstance(node, dict):
        return None

    lat = lon = None

    if 'latitudeE7' in node and 'longitudeE7' in node:
        try:
            lat = float(node['latitudeE7']) / 1e7
            lon = float(node['longitudeE7']) / 1e7
        except (TypeError, ValueError):
            return None
    elif 'latE7' in node and 'lngE7' in node:
        try:
            lat = float(node['latE7']) / 1e7
            lon = float(node['lngE7']) / 1e7
        except (TypeError, ValueError):
            return None
    elif 'latitude' in node and 'longitude' in node:
        try:
            lat = float(node['latitude'])
            lon = float(node['longitude'])
        except (TypeError, ValueError):
            return None
    elif 'latitudeDegrees' in node and 'longitudeDegrees' in node:
        try:
            lat = float(node['latitudeDegrees'])
            lon = float(node['longitudeDegrees'])
        except (TypeError, ValueError):
            return None
    elif 'latLng' in node:
        parsed = parse_lat_lng_value(node.get('latLng'))
        if parsed:
            lat, lon = parsed

    if lat is None or lon is None:
        return None

    return lat, lon

def extract_lat_lon_from_children(node):
    """Extract coordinates from known child nodes or immediate dict children."""
    if not isinstance(node, dict):
        return None

    for key in ('point', 'placePoint', 'placeLocation', 'location'):
        child = node.get(key)
        lat_lon = extract_lat_lon_from_node(child)
        if lat_lon:
            return lat_lon

    for value in node.values():
        if isinstance(value, dict):
            lat_lon = extract_lat_lon_from_node(value)
            if lat_lon:
                return lat_lon
    return None

def collect_timeline_points(node, points):
    """Recursively collect (timestamp, latitude, longitude) tuples from Timeline JSON."""
    if isinstance(node, dict):
        lat_lon = extract_lat_lon_from_node(node)
        if not lat_lon:
            lat_lon = extract_lat_lon_from_children(node)
        if lat_lon:
            timestamps = []
            for key, value in node.items():
                if key.lower() in TIMELINE_TIMESTAMP_KEYS:
                    ts = parse_timestamp_value(value)
                    if ts:
                        timestamps.append(ts)
            for ts in timestamps:
                points.append((ts, lat_lon[0], lat_lon[1]))

        for value in node.values():
            collect_timeline_points(value, points)
    elif isinstance(node, list):
        for item in node:
            collect_timeline_points(item, points)

def collect_device_location_history_points(payload, points):
    """Collect points from on-device location-history.json export format."""
    if not isinstance(payload, list):
        return

    for item in payload:
        if not isinstance(item, dict):
            continue

        start_ts = parse_timestamp_value(item.get('startTime'))
        end_ts = parse_timestamp_value(item.get('endTime'))

        visit = item.get('visit')
        if isinstance(visit, dict):
            top_candidate = visit.get('topCandidate', {})
            visit_point = top_candidate.get('placeLocation') or top_candidate.get('point')
            lat_lon = parse_lat_lng_value(visit_point)
            if lat_lon:
                if start_ts and end_ts:
                    ts = start_ts + (end_ts - start_ts) / 2
                else:
                    ts = start_ts or end_ts
                if ts:
                    points.append((ts, lat_lon[0], lat_lon[1]))

        timeline_path = item.get('timelinePath')
        if isinstance(timeline_path, dict):
            timeline_path = [timeline_path]
        if not isinstance(timeline_path, list):
            continue

        for segment in timeline_path:
            if not isinstance(segment, dict):
                continue

            path_point = segment.get('point') or segment.get('placeLocation')
            lat_lon = parse_lat_lng_value(path_point) or extract_lat_lon_from_node(segment)
            if not lat_lon:
                continue

            ts = start_ts or end_ts
            offset_minutes = segment.get('durationMinutesOffsetFromStartTime')
            if start_ts and offset_minutes is not None:
                try:
                    ts = start_ts + timedelta(minutes=float(offset_minutes))
                except (TypeError, ValueError):
                    ts = start_ts

            if ts:
                points.append((ts, lat_lon[0], lat_lon[1]))

def load_timeline_points(timeline_path):
    """Load timeline points from one JSON file or all JSON files in a directory."""
    candidates = []
    if os.path.isdir(timeline_path):
        for dirpath, _, files in os.walk(timeline_path):
            for filename in files:
                if filename.lower().endswith('.json'):
                    candidates.append(os.path.join(dirpath, filename))
    elif os.path.isfile(timeline_path):
        candidates.append(timeline_path)
    else:
        print(f"Timeline path not found: {timeline_path}")
        return [], 0

    points = []
    for candidate in sorted(candidates):
        try:
            with open(candidate, 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
            collect_device_location_history_points(payload, points)
            collect_timeline_points(payload, points)
        except (OSError, json.JSONDecodeError) as error:
            print(f"Could not parse timeline file {candidate}: {error}")

    unique_points = {}
    for ts, lat, lon in points:
        key = (int(ts.timestamp()), round(lat, 7), round(lon, 7))
        unique_points[key] = (ts, lat, lon)

    sorted_points = sorted(unique_points.values(), key=lambda point: point[0])
    return sorted_points, len(candidates)

def parse_exif_datetime_to_utc(dt_string):
    """Parse EXIF datetime formats into UTC for Timeline matching."""
    if not dt_string:
        return None

    raw = dt_string.strip()
    # Remove sub-second fragments so strptime remains predictable.
    normalized = re.sub(r'(\d{2}:\d{2}:\d{2})\.\d+', r'\1', raw)
    normalized = re.sub(r'(\d{2}T\d{2}:\d{2}:\d{2})\.\d+', r'\1', normalized)
    normalized = normalized.replace('Z', '+00:00')

    formats = [
        '%Y:%m:%d %H:%M:%S%z',
        '%Y:%m:%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(normalized, fmt)
            if dt.tzinfo is None:
                local_tz = datetime.now().astimezone().tzinfo
                dt = dt.replace(tzinfo=local_tz)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None

def find_best_image_timestamp(exif_data):
    """Pick best available image timestamp for Timeline lookup."""
    for key in ('DateTimeOriginal', 'CreateDate', 'MediaCreateDate', 'TrackCreateDate', 'FileModifyDate'):
        timestamp = parse_exif_datetime_to_utc(exif_data.get(key))
        if timestamp:
            return timestamp
    return None

def find_closest_timeline_point(image_timestamp, timeline_points, timeline_timestamps, max_gap_minutes):
    """Find nearest timeline point within max gap."""
    if not timeline_points or image_timestamp is None:
        return None

    index = bisect_left(timeline_timestamps, image_timestamp)
    candidates = []
    if index < len(timeline_points):
        candidates.append(timeline_points[index])
    if index > 0:
        candidates.append(timeline_points[index - 1])
    if not candidates:
        return None

    closest = min(candidates, key=lambda point: abs((point[0] - image_timestamp).total_seconds()))
    if abs((closest[0] - image_timestamp).total_seconds()) <= max_gap_minutes * 60:
        return closest
    return None

def is_supported_image_file(filename):
    return os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS

def format_utc_timestamp(dt):
    """Format datetime in a stable UTC representation for logs."""
    if not dt:
        return "n/a"
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

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
            return parse_gps_and_date_data(xmp_data, parse_gps, parse_date, parse_description)
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


def write_gps_and_creation_date_and_description_to_exif(image_path, gps_data, dry_run, keep_original, overwrite_existing_gps=False, existing_exif_data=None):
    """Write GPS coordinates, CreationDate, and Description to EXIF metadata using ExifTool."""
    if verbose:
        print(f"{image_path}:")
    else: 
        sys.stdout.write(".") 
        sys.stdout.flush()
    if not gps_data:
        print("No GPS data, CreationDate, or Description to write.")
        return
    
    exif_data = existing_exif_data or read_existing_exif_data(image_path)
    if not exif_data:
        print(f"Could not read existing EXIF data for {image_path}.")
        return
    
    data_written = []
    command = ['exiftool']
    if not keep_original:
        command.append('-overwrite_original')

    # Compare GPS data
    if 'latitude' in gps_data and 'longitude' in gps_data:
        existing_latitude = exif_data.get('GPSLatitude')
        existing_longitude = exif_data.get('GPSLongitude')
        existing_latitude_ref = exif_data.get('GPSLatitudeRef', 'N')
        existing_longitude_ref = exif_data.get('GPSLongitudeRef', 'E')

        if existing_latitude and existing_longitude:
            if not overwrite_existing_gps:
                exif_latitude, exif_longitude = convert_exif_gps_to_decimal(
                    existing_latitude,
                    existing_latitude_ref,
                    existing_longitude,
                    existing_longitude_ref
                )
                if verbose and exif_latitude is not None and exif_longitude is not None:
                    print(
                        f" - Existing GPS found, keeping existing value "
                        f"lat={exif_latitude:.7f} lon={exif_longitude:.7f} (use --force to overwrite)."
                    )
                elif verbose:
                    print(" - Existing GPS found, keeping existing value (use --force to overwrite).")
            else:
                exif_latitude, exif_longitude = convert_exif_gps_to_decimal(
                    existing_latitude,
                    existing_latitude_ref,
                    existing_longitude,
                    existing_longitude_ref
                )

                print(f" - Existing EXIF GPS: lat={exif_latitude}, long={exif_longitude}") if verbose else None

                if (round(exif_latitude, 6) == round(gps_data['latitude'], 6) and
                    round(exif_longitude, 6) == round(gps_data['longitude'], 6)):
                    print(f" - GPS data not changed, Skipped.") if verbose else None
                else:
                    print(f"\r - New GPS: lat={gps_data['latitude']}, long={gps_data['longitude']}")
                    command.extend([
                        f"-GPSLatitude={gps_data['latitude']}",
                        f"-GPSLongitude={gps_data['longitude']}",
                        f"-GPSLatitudeRef={gps_data['latitude_ref']}",
                        f"-GPSLongitudeRef={gps_data['longitude_ref']}",
                    ])
                    data_written.append("GPS")
        else:
            print(f"\r - New GPS: lat={gps_data['latitude']}, long={gps_data['longitude']}")
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
            print(f" - Existing EXIF CreationDate: {normalized_existing_date}") if verbose else None

            if normalized_existing_date == gps_data['creation_date']:
                print(f" - CreationDate not changed, Skipped.") if verbose else None
            else:
                print(f" - New CreationDate: {gps_data['creation_date']}")
                command.append(f"-CreateDate={gps_data['creation_date']}")
                data_written.append("CreationDate")
        else:
            print(f" - New CreationDate: {gps_data['creation_date']}")
            command.append(f"-CreateDate={gps_data['creation_date']}")
            data_written.append("CreationDate")

    # Handle Description
    if 'description' in gps_data:
        existing_description = exif_data.get('ImageDescription')
        if existing_description:
            print(f" - Existing EXIF Description: {existing_description}") if verbose else None

            if existing_description == gps_data['description']:
                print(f" - Description not changed, Skipped.") if verbose else None
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
            command.append(image_path)
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"=> {', '.join(data_written)} data written to {os.path.basename(image_path)}")
                return ', '.join(data_written)
            else:
                print(f"Error writing metadata to {image_path}: {result.stderr}")
                return None
    return 'skipped'

def find_image_file(base_filename, dirpath):
    """Find image files with the same base name, prioritizing no extension first."""
    possible_extensions = ['', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.cr3']  # No extension first
    
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
    
def process_images_and_sidecars(root_directory, delete_xmp, backup_directory, parse_gps, parse_date, dry_run, parse_description, keep_original, force):
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
                        result = write_gps_and_creation_date_and_description_to_exif(
                            image_path,
                            gps_data,
                            dry_run,
                            keep_original,
                            overwrite_existing_gps=force
                        )
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
    
    return {
        'total_files': total_files,
        'total_skipped': total_skipped,
        'total_written_gps': total_written_gps,
        'total_written_date': total_written_date,
        'total_written_description': total_written_description,
    }

def process_images_from_timeline(root_directory, timeline_points, max_gap_minutes, dry_run, keep_original, force):
    """Apply GPS from Timeline to image files missing GPS metadata."""
    total_images = 0
    total_missing_timestamp = 0
    total_no_match = 0
    total_written = 0
    total_skipped_has_gps = 0

    timeline_timestamps = [point[0] for point in timeline_points]

    for dirpath, _, files in os.walk(root_directory):
        for file in files:
            if not is_supported_image_file(file):
                continue

            total_images += 1
            image_path = os.path.join(dirpath, file)
            exif_data = read_existing_exif_data(image_path)
            if not exif_data:
                continue

            if exif_data.get('GPSLatitude') and exif_data.get('GPSLongitude') and not force:
                total_skipped_has_gps += 1
                if verbose:
                    existing_latitude = exif_data.get('GPSLatitude')
                    existing_longitude = exif_data.get('GPSLongitude')
                    existing_latitude_ref = exif_data.get('GPSLatitudeRef', 'N')
                    existing_longitude_ref = exif_data.get('GPSLongitudeRef', 'E')
                    exif_latitude, exif_longitude = convert_exif_gps_to_decimal(
                        existing_latitude,
                        existing_latitude_ref,
                        existing_longitude,
                        existing_longitude_ref
                    )
                    if exif_latitude is not None and exif_longitude is not None:
                        print(
                            f"{image_path}: existing GPS found, skipping "
                            f"lat={exif_latitude:.7f} lon={exif_longitude:.7f}"
                        )
                    else:
                        print(f"{image_path}: existing GPS found, skipping")
                continue

            image_timestamp = find_best_image_timestamp(exif_data)
            if not image_timestamp:
                total_missing_timestamp += 1
                continue

            closest = find_closest_timeline_point(image_timestamp, timeline_points, timeline_timestamps, max_gap_minutes)
            if not closest:
                total_no_match += 1
                if verbose:
                    print(
                        f"{image_path}: no timeline point within {max_gap_minutes} min "
                        f"for image time {format_utc_timestamp(image_timestamp)}"
                    )
                continue

            timeline_timestamp, lat, lon = closest
            delta_seconds = abs((timeline_timestamp - image_timestamp).total_seconds())
            if verbose:
                print(
                    f"{image_path}: timeline match image={format_utc_timestamp(image_timestamp)} "
                    f"timeline={format_utc_timestamp(timeline_timestamp)} "
                    f"delta={delta_seconds/60:.1f} min lat={lat:.7f} lon={lon:.7f}"
                )
            gps_data = {
                'latitude': abs(lat),
                'longitude': abs(lon),
                'latitude_ref': 'N' if lat >= 0 else 'S',
                'longitude_ref': 'E' if lon >= 0 else 'W',
            }
            result = write_gps_and_creation_date_and_description_to_exif(
                image_path,
                gps_data,
                dry_run,
                keep_original,
                overwrite_existing_gps=force,
                existing_exif_data=exif_data
            )
            if result and 'GPS' in result:
                total_written += 1

    return {
        'total_images': total_images,
        'total_skipped_has_gps': total_skipped_has_gps,
        'total_missing_timestamp': total_missing_timestamp,
        'total_no_match': total_no_match,
        'total_written': total_written,
    }


def main():
    parser = argparse.ArgumentParser(description="Process images and sidecar XMP files to transfer metadata.")
    parser.add_argument('root_directory', type=str, help="Root directory to process.")
    parser.add_argument('--delete', action='store_true', help="Delete XMP sidecar files after processing.")
    parser.add_argument('--nobackup', action='store_true', help="No need for backup.")
    parser.add_argument('--backup', type=str, help="Backup location for XMP sidecar files.")
    parser.add_argument('--keep', action='store_true', help="Keep a copy of the file as _original.")
    parser.add_argument('--gps', action='store_true', help="Only parse GPS data from sidecar files.")
    parser.add_argument('--date', action='store_true', help="Only parse CreationDate from sidecar files.")
    parser.add_argument('--description', action='store_true', help="Only parse Description from sidecar files.")
    parser.add_argument('--timeline', type=str, help="Google Timeline JSON file or directory containing Timeline JSON exports.")
    parser.add_argument('--timeline-max-gap', type=int, default=30, help="Maximum minute difference allowed between image timestamp and Timeline point (default: 30).")
    parser.add_argument('--force', action='store_true', help="Overwrite existing GPS coordinates. Default keeps existing GPS.")
    parser.add_argument('--dryrun', action='store_true', help="Run the script without making any changes.")
    parser.add_argument('-v','--verbose', action='store_true', help="Log more details to console.")

    args = parser.parse_args()
    global verbose 
    verbose = args.verbose
    print ("<verbose>") if verbose else print (".", end="") 
    # If no --GPS or --Date is specified, default to both
    parse_gps = args.gps or (not args.date and not args.description)
    parse_date = args.date or (not args.gps and not args.description)
    parse_description = args.description or (not args.gps and not args.date)

    if not args.backup and (not args.nobackup):
        print("""\rError: Use the `--backup <folder>` option to specify a backup location where files will be copied before changes are made.

Backup of changed and deleted files to another folder is recommended. If you choose not to create a backup, use the `--nobackup` parameter to indicate that no backup should be performed.

Tip: Use the `--dryrun` option to verify what actions would be taken without making any actual changes. This can help you confirm the behavior of the script before performing the actual operations.
""")
        sys.exit(1)

    sidecar_summary = process_images_and_sidecars(
        args.root_directory,
        args.delete,
        args.backup,
        parse_gps,
        parse_date,
        args.dryrun,
        parse_description,
        args.keep,
        args.force
    )

    timeline_summary = None
    timeline_points = []
    timeline_file_count = 0
    if args.timeline:
        if not parse_gps:
            print("Timeline processing skipped because GPS parsing is disabled.")
        else:
            timeline_points, timeline_file_count = load_timeline_points(args.timeline)
            if not timeline_points:
                print("No usable timeline points found.")
            else:
                print(f"Loaded {len(timeline_points)} timeline points from {timeline_file_count} file(s).")
                print(
                    "Timeline range: "
                    f"{format_utc_timestamp(timeline_points[0][0])} -> "
                    f"{format_utc_timestamp(timeline_points[-1][0])}"
                )
                timeline_summary = process_images_from_timeline(
                    args.root_directory,
                    timeline_points,
                    args.timeline_max_gap,
                    args.dryrun,
                    args.keep,
                    args.force
                )

    print("\nSummary:")
    print(f"Total files processed: {sidecar_summary['total_files']}")
    print(f"Total files skipped: {sidecar_summary['total_skipped']}")
    print(f"Total files written (GPS): {sidecar_summary['total_written_gps']}")
    print(f"Total files written (CreationDate): {sidecar_summary['total_written_date']}")
    print(f"Total files written (Description): {sidecar_summary['total_written_description']}")

    if args.timeline:
        print("\nTimeline summary:")
        if timeline_summary:
            print(f"Images scanned: {timeline_summary['total_images']}")
            print(f"Images skipped (already had GPS): {timeline_summary['total_skipped_has_gps']}")
            print(f"Images skipped (missing timestamp): {timeline_summary['total_missing_timestamp']}")
            print(f"Images skipped (no timeline match): {timeline_summary['total_no_match']}")
            print(f"Images written (GPS from Timeline): {timeline_summary['total_written']}")

if __name__ == "__main__":
    main()
