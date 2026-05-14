#!/usr/bin/env python3
import os
import zipfile
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from io import BytesIO
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Path to your service account JSON file (you'll upload it to PythonAnywhere)
SERVICE_ACCOUNT_FILE = 'sleep-tracker-credentials.json'

def get_drive_service():
    """Authenticate with Google Drive"""
    scopes = ['https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes)
    return build('drive', 'v3', credentials=credentials)

def get_sheets_service():
    """Authenticate with Google Sheets"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes)
    return gspread.authorize(credentials)

def find_latest_zip(drive_service):
    """Find the latest Connessione Salute ZIP in Drive"""
    query = "name contains 'Connessione Salute' and mimeType='application/zip'"
    results = drive_service.files().list(
        q=query, spaces='drive', pageSize=1, 
        orderBy='modifiedTime desc', fields='files(id, name, modifiedTime)'
    ).execute()
    
    files = results.get('files', [])
    if not files:
        print("No ZIP file found")
        return None
    return files[0]

def download_zip_content(drive_service, file_id):
    """Download ZIP file content as bytes"""
    request = drive_service.files().get_media(fileId=file_id)
    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()

def extract_sleep_data(zip_bytes):
    """Extract sleep data from the SQLite database in the ZIP"""
    with zipfile.ZipFile(BytesIO(zip_bytes)) as z:
        db_content = z.read('health_connect_export.db')
    
    db = sqlite3.connect(':memory:')
    db.executescript(db_content.decode('latin-1') if isinstance(db_content, bytes) else db_content)
    c = db.cursor()
    
    # Get most recent sleep session
    c.execute('''
        SELECT s.row_id, s.start_time, s.end_time,
            datetime(s.start_time/1000, 'unixepoch', '+2 hours') as bedtime
        FROM sleep_session_record_table s
        ORDER BY s.start_time DESC
    ''')
    
    for sess in c.fetchall():
        row_id, start_ms, end_ms, bedtime = sess
        total_sec = int((end_ms - start_ms)/1000)
        
        # Skip naps (< 2 hours) and sessions with no sleep stages
        c.execute('SELECT COUNT(*) FROM sleep_stages_table WHERE parent_key=?', (row_id,))
        stage_count = c.fetchone()[0]
        if total_sec < 7200 or stage_count == 0:
            continue
        
        # Get sleep stages
        c.execute('''
            SELECT stage_type, round(SUM((stage_end_time-stage_start_time)/1000/60.0),0)
            FROM sleep_stages_table WHERE parent_key=? GROUP BY stage_type
        ''', (row_id,))
        stages = {r[0]: int(r[1]) for r in c.fetchall()}
        
        # Get HRV
        c.execute('''
            SELECT round(AVG(heart_rate_variability_millis),1)
            FROM heart_rate_variability_rmssd_record_table
            WHERE time/1000 BETWEEN ? AND ?
        ''', (start_ms/1000, end_ms/1000))
        hrv = c.fetchone()[0] or ''
        
        awake = stages.get(1, 0)
        light = stages.get(4, 0)
        deep = stages.get(5, 0)
        rem = stages.get(6, 0)
        slept_sec = total_sec - awake*60
        
        db.close()
        return [bedtime, slept_sec, awake*60, deep, rem, light, hrv, '', '', '', '', '', '', '']
    
    db.close()
    return None

def append_to_sheet(sheets_service, row_data, sheet_id):
    """Append row to Google Sheet"""
    try:
        sheet = sheets_service.open_by_key(sheet_id)
        worksheet = sheet.sheet1
        
        # Check if row already exists (by checking if FellAsleepAt is already in sheet)
        existing = worksheet.col_values(1)  # Get all values in first column
        if row_data[0] in existing:
            print(f"Row for {row_data[0]} already exists, skipping")
            return False
        
        # Insert row at position 2 (after header)
        worksheet.insert_row(row_data, index=2)
        print(f"Row added: {row_data[0]}")
        return True
    except Exception as e:
        print(f"Error appending to sheet: {e}")
        return False

def main():
    try:
        print("Starting sleep data update...")
        
        # Get services
        drive_service = get_drive_service()
        sheets_service = get_sheets_service()
        
        # Find latest ZIP
        print("Finding latest Health Connect ZIP...")
        zip_file = find_latest_zip(drive_service)
        if not zip_file:
            print("No ZIP file found")
            return
        
        print(f"Found: {zip_file['name']}")
        
        # Download and extract
        print("Downloading and processing data...")
        zip_bytes = download_zip_content(drive_service, zip_file['id'])
        row_data = extract_sleep_data(zip_bytes)
        
        if not row_data:
            print("No valid sleep data found")
            return
        
        print(f"Extracted data: {row_data[0]}")
        
        # Append to sheet (replace with your actual Sheet ID)
        SHEET_ID = '1UxGUc_1j874ewUzk-ewjtvuZppawsswQyXz9XOKJBKk'
        append_to_sheet(sheets_service, row_data, SHEET_ID)
        
        print("Done!")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()