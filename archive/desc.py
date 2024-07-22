import subprocess

def get_description_with_exiftool(image_path):
    try:
        result = subprocess.run(
            ['exiftool', '-ImageDescription', '-XPCommnt', '-Description', image_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return f"Error: {result.stderr}"
    except FileNotFoundError:
        return "ExifTool is not installed or not found in PATH."

def display_description(metadata):
    if metadata.strip():  # Check if the metadata is not empty
        print("Description Metadata:")
        print(metadata)
    else:
        print("No description metadata found.")

# Path to your image file
image_path = '/Users/peter/tmp/05/20010517_191745_CB2E863A.jpg'

# Get description metadata
description_metadata = get_description_with_exiftool(image_path)

# Display description metadata
display_description(description_metadata)

