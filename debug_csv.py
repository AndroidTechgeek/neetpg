# debug_csv.py
import pandas as pd
import chardet
import sys

def analyze_csv(filename):
    """
    Analyzes a CSV file to detect its encoding and check for common errors.
    """
    print(f"\n--- Starting Analysis for: {filename} ---\n")

    # --- Step 1: Check if file exists ---
    try:
        with open(filename, 'rb') as f:
            raw_data = f.read()
        print("✅ Step 1: File found and opened successfully.")
    except FileNotFoundError:
        print(f"❌ ERROR: File '{filename}' not found. Please make sure it's in the same folder as this script.")
        return

    # --- Step 2: Detect File Encoding ---
    try:
        detection_result = chardet.detect(raw_data)
        detected_encoding = detection_result['encoding']
        confidence = detection_result['confidence']
        print(f"✅ Step 2: Encoding detection complete.")
        print(f"    - Detected Encoding: {detected_encoding}")
        print(f"    - Confidence Level: {confidence * 100:.2f}%")
        
        if confidence < 0.9:
            print("    - WARNING: Low confidence in encoding detection. The file might have mixed encodings or be corrupt.")

    except Exception as e:
        print(f"❌ ERROR: Could not detect file encoding. Error: {e}")
        return

    # --- Step 3: Attempt to Read with Detected Encoding ---
    try:
        # Use the detected encoding to read the file
        df = pd.read_csv(filename, encoding=detected_encoding)
        print(f"\n✅ Step 3: Successfully read the file using '{detected_encoding}' encoding.")
        print("    - File appears to be a valid CSV.")
        print(f"    - Number of rows found: {len(df)}")
        print(f"    - Column headers found: {list(df.columns)}")
        print("\n--- Analysis Complete: The file seems OK! ---")
        print("If the app still fails, the problem might be with specific data inside the file, not the file format itself.")

    except UnicodeDecodeError as e:
        print(f"\n❌ ERROR: A UnicodeDecodeError occurred even with the detected encoding.")
        print("    - This means the file is likely NOT saved as the detected encoding or contains mixed/corrupt characters.")
        print(f"    - Details: {e}")
        print("\n--- RECOMMENDATION ---")
        print("Please re-open this CSV in Excel and save it again, explicitly choosing 'CSV UTF-8'.")

    except Exception as e:
        print(f"\n❌ ERROR: An unexpected error occurred while reading the CSV with pandas.")
        print(f"    - Details: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        filename_to_check = sys.argv[1]
        analyze_csv(filename_to_check)
    else:
        print("\nUsage: Please provide the filename to check.")
        print("Example: python debug_csv.py ANAT4.CSV")