#!/usr/bin/env python3
"""Generate favicon files from the logo.png for the web app."""

from PIL import Image
import os

# Source logo and output directory
logo_path = "docs/images/logo.png"
output_dir = "homelab_storage_monitor/web/static"
sizes = [16, 32, 180, 192, 512]


if __name__ == '__main__':
    # Load the source logo
    logo = Image.open(logo_path)

    # Convert to RGBA if needed
    if logo.mode != 'RGBA':
        logo = logo.convert('RGBA')

    print(f"Source logo: {logo.size[0]}x{logo.size[1]}")

    # Generate favicons
    os.makedirs(output_dir, exist_ok=True)

    for size in sizes:
        # Resize with high-quality resampling
        img = logo.resize((size, size), Image.Resampling.LANCZOS)

        if size == 16:
            img.save(os.path.join(output_dir, 'favicon-16x16.png'), 'PNG')
        elif size == 32:
            img.save(os.path.join(output_dir, 'favicon-32x32.png'), 'PNG')
        elif size == 180:
            img.save(os.path.join(output_dir, 'apple-touch-icon.png'), 'PNG')
        elif size == 192:
            img.save(os.path.join(output_dir, 'favicon-192x192.png'), 'PNG')
        elif size == 512:
            img.save(os.path.join(output_dir, 'favicon-512x512.png'), 'PNG')

        print(f"  Created {size}x{size} favicon")

    # Create ICO file
    ico_img = logo.resize((32, 32), Image.Resampling.LANCZOS)
    ico_img.save(os.path.join(output_dir, 'favicon.ico'), 'ICO')
    print("  Created favicon.ico")

    print("\nFavicons created successfully!")
    for f in sorted(os.listdir(output_dir)):
        print(f"  - {f}")
        print(f"  - {f}")
