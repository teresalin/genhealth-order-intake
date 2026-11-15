"""Generate image-only synthetic DME order PDFs for failure-scenario testing (all data fictional).

NPIs: 1230004560 and 1230004529 have valid check digits and returned 0 results in NPPES;
1230004561 deliberately fails the check digit.
"""
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
W, H = 1275, 1650  # letter at 150 dpi
FONT = ImageFont.load_default(size=30)
BIG = ImageFont.load_default(size=40)


def page(lines, title=None):
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    y = 90
    if title:
        d.text((90, y), title, font=BIG, fill=0)
        y += 90
    for line in lines:
        d.text((90, y), line, font=FONT, fill=0)
        y += 52
    return img


def save(name, pages):
    path = os.path.join(OUT, name)
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=150)
    print("wrote", path)


# S1: DOB and NPI left blank on the form.
save("S1_missing_dob_and_npi.pdf", [page([
    "Patient First Name: Olivia          Last Name: Carter",
    "Date of Birth: ____ / ____ / ________",
    "Address: 100 Example St, Some City, CA 12345",
    "",
    "Item ordered: Standard folding walker (E0135), qty 1",
    "Length of need: 99 months",
    "",
    "Ordering Physician: Maya Chen, MD",
    "NPI: ______________________",
    "Physician Signature: Maya Chen, MD        Date: 03/01/2026",
], "ACME MEDICAL SUPPLY - DME ORDER FORM")])

# S2: NPI fails the check digit; provider written without credentials; patient written LAST, FIRST.
save("S2_invalid_npi_no_credentials.pdf", [page([
    "Patient: CARTER, OLIVIA",
    "DOB: 02/02/1990",
    "",
    "Equipment: CPAP device (E0601) with heated humidifier",
    "",
    "Prescriber: Maya Chen",
    "Prescriber NPI: 1230004561",
    "Signature: Maya Chen                      Date: 03/01/2026",
], "PRESCRIPTION - STANDARD WRITTEN ORDER")])

# S3: two pages, conflicting DOBs, several physicians in different roles (referring one has an NPI too).
save("S3_conflicting_dob_multi_provider.pdf", [
    page([
        "Patient Name: Olivia Carter",
        "DOB: 02/02/1990",
        "",
        "Referring Physician: Samuel Ortiz, MD",
        "Referring NPI: 1230004529",
        "Primary Care Physician: Dr. Priya Nair",
        "Reason for referral: progressive knee osteoarthritis, needs rollator",
    ], "REFERRAL COVER SHEET"),
    page([
        "Patient: Olivia Carter",
        "Date of Birth: 02/20/1990",
        "",
        "Item: Rollator walker with seat (E0149), qty 1",
        "",
        "Ordering Physician: Maya Chen, MD",
        "Ordering Physician NPI: 1230004560",
        "Signature: Maya Chen, MD                  Date: 03/02/2026",
    ], "STANDARD WRITTEN ORDER"),
])

# S4: complete order, but scanned badly: low resolution, blurred, noisy, rotated 90 degrees plus skew.
src = page([
    "Patient First Name: Olivia     Last Name: Carter",
    "DOB: 02/02/1990",
    "Referring Physician: Samuel Ortiz, MD",
    "",
    "Item: Hospital bed, semi-electric (E0260)",
    "",
    "Ordering Physician: Maya Chen, MD",
    "NPI: 1230004560",
    "Signature: Maya Chen, MD                  Date: 03/03/2026",
], "HOME MEDICAL EQUIPMENT ORDER")
low = src.resize((W // 3, H // 3), Image.BILINEAR).resize((W, H), Image.NEAREST)
low = low.filter(ImageFilter.GaussianBlur(1.4))
random.seed(7)
px = low.load()
for _ in range(60000):
    x, y = random.randrange(W), random.randrange(H)
    px[x, y] = random.choice((0, 255))
skewed = low.rotate(2.5, expand=False, fillcolor=255)
rotated = skewed.rotate(90, expand=True, fillcolor=255)  # sideways page
save("S4_rotated_blurry_lowres.pdf", [rotated])
