from PIL import Image
img = Image.open("assets/character-demon-bear.jpg")
img.save("assets/character-demon-bear.png")
print("Converted JPEG to PNG successfully")
