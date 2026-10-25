# PyInstaller build for sc-locator.exe, with Tesseract packed inside.
#   pip install pyinstaller
#   pyinstaller sc_locator.spec
# The Tesseract engine comes in with tesserocr (pip install -r requirements.txt);
# only its language data is copied from a local Tesseract install
# (winget install UB-Mannheim.TesseractOCR). Set TESSERACT_DIR if it is not in
# the default place.
import os

TESS = os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR")

datas = [
    (os.path.join(TESS, "tessdata", "eng.traineddata"), "tesseract/tessdata"),
    (os.path.join(TESS, "doc", "LICENSE"), "tesseract"),
    ("icon.ico", "."),          # the windows' icon (make_icon.py draws it)
]

a = Analysis(["sc_locator.py"], datas=datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="sc-locator",
          console=True, upx=False, icon="icon.ico")
