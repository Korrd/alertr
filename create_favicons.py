#!/usr/bin/env python3
"""Generate favicon files for the web app."""

from PIL import Image, ImageDraw
import os

# Create favicon from the logo concept (brushstroke "A" on dark background)
sizes = [16, 32, 180, 192, 512]
output_dir = "homelab_storage_monitor/web/static"


def create_favicon(size):
    """Create a simple 'A' favicon matching the brushstroke style."""
    img = Image.new('RGBA', (size, size), (10, 10, 10, 255))
    draw = ImageDraw.Draw(img)

    # Draw a stylized "A" - simplified for small sizes
    margin = size * 0.1
    center_x = size / 2
    top_y = margin
    bottom_y = size - margin
    width = size * 0.7

    # Cream/off-white color matching the logo
    cream = (245, 240, 232, 255)
    stroke_width = max(2, int(size * 0.12))

    # Left leg of A
    left_start = (center_x, top_y)
    left_end = (center_x - width/2, bottom_y)
    draw.line([left_start, left_end], fill=cream, width=stroke_width)

    # Right leg of A
    right_start = (center_x, top_y)
    right_end = (center_x + width/2, bottom_y)
    draw.line([right_start, right_end], fill=cream, width=stroke_width)

    # Crossbar
    crossbar_y = size * 0.6
    crossbar_left = center_x - width * 0.3
    crossbar_right = center_x + width * 0.3
    draw.line([(crossbar_left, crossbar_y), (crossbar_right, crossbar_y)],
              fill=cream, width=max(1, stroke_width - 1))

    return img


if __name__ == '__main__':
    # Generate favicons
    os.makedirs(output_dir, exist_ok=True)

    for size in sizes:
        img = create_favicon(size)
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

    # Create ICO file with multiple sizes
    ico_img = create_favicon(32)
    ico_img.save(os.path.join(output_dir, 'favicon.ico'), 'ICO')

    print("Favicons created successfully!")
    for f in sorted(os.listdir(output_dir)):
        print(f"  - {f}")
