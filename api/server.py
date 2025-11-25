from fastapi import FastAPI, UploadFile, File, HTTPException
import os
from processor.extracter import extract_coords_combined, find_sender_receiver

app = FastAPI(title="Coordinate_extraction")

@app.post("/upload_file")
async def upload_pdf(file: UploadFile = File(...)):
    # Ensure the uploaded file is a PDF
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Upload a PDF file")

    # Save PDF temporarily
    file_location = f"temp_{file.filename}"
    with open(file_location, "wb") as f:
        f.write(await file.read())

    try:
        # Process PDF
        rectangles_grouped = extract_coords_combined(file_location, output_dir="out")
        addresses = find_sender_receiver([
            r for line in rectangles_grouped for r in line.values()
        ])

        return {
            "sender_blocks": addresses["sender"],
            "receiver_blocks": addresses["receiver"]
        }

    finally:
        # Clean up
        if os.path.exists(file_location):
            os.remove(file_location)






