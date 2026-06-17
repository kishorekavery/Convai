'''Test table view'''
# import os
# from dotenv import load_dotenv
# import psycopg2

# # Load environment variables from the .env file
# load_dotenv()

# def get_database_schema():
#     # Establish connection using environment variables
#     try:
#         connection = psycopg2.connect(
#             host=os.getenv("DB_HOST"),
#             port=os.getenv("DB_PORT"),
#             database="parry",
#             user=os.getenv("DB_USERNAME"),
#             password=os.getenv("DB_PASSWORD")
#         )
#         cursor = connection.cursor()

#         # Query to fetch tables and column details
#         schema_query = """
#         SELECT * FROM public.user_ai_quota;
#         """

#         cursor.execute(schema_query)
#         rows = cursor.fetchall()

#         # Get column names
#         colnames = [desc[0] for desc in cursor.description]
#         print(f"\n📋 Table: public.user_ai_quota")
#         print("-" * 95)
#         print(" | ".join(f"{col:<13}" for col in colnames))
#         print("-" * 95)

#         # Display the rows
#         for row in rows:
#             print(" | ".join(f"{str(val):<13}" for val in row))

#     except Exception as error:
#         print(f"❌ Error connecting to PostgreSQL: {error}")

#     finally:
#         if 'connection' in locals() and connection:
#             cursor.close()
#             connection.close()
#             print("\n🔌 Database connection closed.")

# if __name__ == "__main__":
#     get_database_schema()



'''rich progress bar demo'''
# from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

# with Progress(
#     TextColumn("[progress.description]{task.description}"),
#     BarColumn(),
#     TaskProgressColumn(),
#     TimeRemainingColumn(),
# ) as progress:
#     task1 = progress.add_task("[cyan]Downloading...", total=100)
    
#     while not progress.finished:
#         progress.update(task1, advance=1)


'''tenacity demo'''
# import random
# import time
# from tenacity import retry, stop_after_attempt, wait_exponential

# # Global tracker just to show you the attempt numbers in the console
# attempt_counter = 0

# @retry(
#     stop=stop_after_attempt(5),            # Give up after 5 tries
#     wait=wait_exponential(min=2, max=10), # Wait 2s, then 4s, then 8s...
#     reraise=True                          # If attempt #5 fails, raise the actual error
# )
# def fetch_data():
#     global attempt_counter
#     attempt_counter += 1
    
#     print(f"[{time.strftime('%H:%M:%S')}] Attempt #{attempt_counter}: Connecting to server...")
    
#     # Simulate a flaky server: 70% chance of failing, 30% chance of succeeding
#     if random.random() < 0.7:
#         print("❌ Network Timeout! Connection dropped.")
#         raise ConnectionError("Server did not respond in time.")
        
#     print("✅ Success! Data fetched successfully.")
#     return {"status": "success", "data":"data"}

# # --- Execution ---
# try:
#     result = fetch_data()
#     print(f"\nFinal Result: {result}")
# except ConnectionError as e:
#     print(f"\n❌ Execution completely failed after 5 attempts. Error: {e}")

'''Logging demo'''
# import logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s - %(levelname)s - %(message)s',
#     handlers=[
#         logging.FileHandler("app.log"),
#         logging.StreamHandler()
#     ]
# )
# logging.info("Hello world")

'''sys, path, shutil and os basics'''
# import sys
# import os
# import shutil
# import tempfile

# # 1. Use sys to get Python version
# print(f"Python Version: {sys.version}")
# print(f"Executable Path: {sys.executable}")
# print(f"Current Working Directory: {os.getcwd()}")

# # 2. Create and list files using os and tempfile
# temp_dir = tempfile.mkdtemp()
# print(f"Created temporary directory: {temp_dir}")

# file_path = os.path.join(temp_dir, "test_file.txt")
# with open(file_path, "w") as f:
#     f.write("Hello from Python!")

# print(f"Created file: {file_path}")
# print(f"Files in temp dir: {os.listdir(temp_dir)}")

# # 3. Clean up using shutil
# shutil.rmtree(temp_dir)
# print(f"Cleaned up temporary directory: {temp_dir}")

'''functools demo'''
# import time
# @functools.lru_cache(maxsize=None)
# def slow_add(a,b):
#     print(f"Adding {a} + {b}...")
#     time.sleep(2) # Simulate a slow calculation
#     return a + b

# print(slow_add(2,3)) # First call: takes 2 seconds
# print(slow_add(2,3)) # Second call: instant (cached)
# print(slow_add(4,5)) # New inputs: takes 2 seconds

