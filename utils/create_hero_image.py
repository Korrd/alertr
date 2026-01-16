#!/usr/bin/env python3
"""
Create a hero image for the README from dashboard screenshots.
Creates a composite with multiple views arranged attractively.
"""

from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
import os

def add_shadow(image, offset=(20, 20), shadow_color=(0, 0, 0, 100), blur_radius=30):
    """Add a drop shadow to an image."""
    # Create a larger canvas for the shadow
    shadow_size = (
        image.width + abs(offset[0]) + blur_radius * 2,
        image.height + abs(offset[1]) + blur_radius * 2
    )
    shadow = Image.new('RGBA', shadow_size, (0, 0, 0, 0))
    
    # Create shadow shape
    shadow_shape = Image.new('RGBA', (image.width, image.height), shadow_color)
    shadow.paste(shadow_shape, (blur_radius + max(offset[0], 0), blur_radius + max(offset[1], 0)))
    
    # Blur the shadow
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
    
    # Paste original image on top
    shadow.paste(image, (blur_radius + max(-offset[0], 0), blur_radius + max(-offset[1], 0)), image if image.mode == 'RGBA' else None)
    
    return shadow


def add_rounded_corners(image, radius=20):
    """Add rounded corners to an image."""
    # Convert to RGBA if needed
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    
    # Create a mask with rounded corners
    mask = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), image.size], radius=radius, fill=255)
    
    # Apply mask
    output = Image.new('RGBA', image.size, (0, 0, 0, 0))
    output.paste(image, mask=mask)
    
    return output


def add_browser_frame(image, title="Homelab Storage Monitor"):
    """Add a minimal browser-like frame to the image."""
    # Frame settings
    title_bar_height = 40
    border_radius = 12
    frame_color = (30, 30, 35, 255)
    
    # Create frame
    frame_width = image.width + 4
    frame_height = image.height + title_bar_height + 4
    frame = Image.new('RGBA', (frame_width, frame_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(frame)
    
    # Draw frame background with rounded corners
    draw.rounded_rectangle(
        [(0, 0), (frame_width, frame_height)],
        radius=border_radius,
        fill=frame_color
    )
    
    # Draw traffic lights
    light_y = title_bar_height // 2
    lights = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]  # red, yellow, green
    for i, color in enumerate(lights):
        draw.ellipse(
            [(15 + i * 22, light_y - 6), (15 + i * 22 + 12, light_y + 6)],
            fill=color
        )
    
    # Paste the screenshot
    frame.paste(image, (2, title_bar_height + 2))
    
    return frame


def create_hero_image(screenshots_dir, output_path, canvas_width=1200, canvas_height=630):
    """
    Create a hero image from screenshots.
    
    Args:
        screenshots_dir: Directory containing screenshot files
        output_path: Where to save the final hero image
        canvas_width: Width of the final image
        canvas_height: Height of the final image
    """
    # Background gradient (dark theme matching the app)
    canvas = Image.new('RGBA', (canvas_width, canvas_height), (15, 23, 42, 255))
    
    # Add subtle gradient
    draw = ImageDraw.Draw(canvas)
    for y in range(canvas_height):
        # Gradient from slightly lighter at top to darker at bottom
        r = int(15 + (25 - 15) * (1 - y / canvas_height) * 0.5)
        g = int(23 + (35 - 23) * (1 - y / canvas_height) * 0.5)
        b = int(42 + (60 - 42) * (1 - y / canvas_height) * 0.5)
        draw.line([(0, y), (canvas_width, y)], fill=(r, g, b, 255))
    
    # Look for screenshots
    screenshot_files = []
    for ext in ['png', 'jpg', 'jpeg']:
        screenshot_files.extend([
            f for f in os.listdir(screenshots_dir) 
            if f.lower().endswith(f'.{ext}') and 'logo' not in f.lower()
        ])
    
    if not screenshot_files:
        print(f"No screenshots found in {screenshots_dir}")
        print("Please add screenshot files (PNG/JPG) to the directory.")
        return False
    
    print(f"Found screenshots: {screenshot_files}")
    
    # Load and process screenshots
    screenshots = []
    for f in sorted(screenshot_files)[:3]:  # Use up to 3 screenshots
        img = Image.open(os.path.join(screenshots_dir, f))
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        screenshots.append(img)
    
    if len(screenshots) == 1:
        # Single screenshot - center it with frame and shadow
        img = screenshots[0]
        # Scale to fit nicely
        scale = min((canvas_width - 100) / img.width, (canvas_height - 80) / img.height)
        new_size = (int(img.width * scale), int(img.height * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # Add rounded corners and shadow
        img = add_rounded_corners(img, radius=12)
        img = add_shadow(img, offset=(15, 15), blur_radius=25)
        
        # Center on canvas
        x = (canvas_width - img.width) // 2
        y = (canvas_height - img.height) // 2
        canvas.paste(img, (x, y), img)
        
    elif len(screenshots) >= 2:
        # Multiple screenshots - create overlapping layout
        # Main screenshot (larger, slightly left)
        main_img = screenshots[0]
        main_scale = min((canvas_width * 0.7) / main_img.width, (canvas_height - 60) / main_img.height)
        main_size = (int(main_img.width * main_scale), int(main_img.height * main_scale))
        main_img = main_img.resize(main_size, Image.Resampling.LANCZOS)
        main_img = add_rounded_corners(main_img, radius=12)
        main_img = add_shadow(main_img, offset=(20, 20), blur_radius=30)
        
        # Secondary screenshot (smaller, overlapping from right)
        sec_img = screenshots[1]
        sec_scale = main_scale * 0.75
        sec_size = (int(sec_img.width * sec_scale), int(sec_img.height * sec_scale))
        sec_img = sec_img.resize(sec_size, Image.Resampling.LANCZOS)
        sec_img = add_rounded_corners(sec_img, radius=10)
        sec_img = add_shadow(sec_img, offset=(15, 15), blur_radius=20)
        
        # Position screenshots
        main_x = 30
        main_y = (canvas_height - main_img.height) // 2
        
        sec_x = canvas_width - sec_img.width - 20
        sec_y = canvas_height - sec_img.height - 30
        
        # Paste in order (back to front)
        canvas.paste(sec_img, (sec_x, sec_y), sec_img)
        canvas.paste(main_img, (main_x, main_y), main_img)
    
    # Save the result
    canvas.save(output_path, 'PNG', quality=95)
    print(f"Hero image saved to: {output_path}")
    return True


if __name__ == '__main__':
    import sys
    
    # Default paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    screenshots_dir = os.path.join(script_dir, 'docs', 'images')
    output_path = os.path.join(script_dir, 'docs', 'images', 'dashboard-preview.png')
    
    # Allow command line overrides
    if len(sys.argv) >= 2:
        screenshots_dir = sys.argv[1]
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    
    print(f"Looking for screenshots in: {screenshots_dir}")
    print(f"Output will be saved to: {output_path}")
    print()
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    success = create_hero_image(screenshots_dir, output_path)
    
    if not success:
        print("\nTo use this script:")
        print("1. Save your dashboard screenshots to docs/images/")
        print("2. Name them like: screenshot-1.png, screenshot-2.png")
        print("3. Run: python create_hero_image.py")
        sys.exit(1)
