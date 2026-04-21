# 1. Start with a lightweight Linux computer that already has Python 3.11 installed
FROM python:3.11-slim

# 2. Create a folder inside this new computer called /app
WORKDIR /app

# 3. Copy your requirements list from your laptop into the /app folder
COPY requirements.txt .

# 4. Tell the computer to install your libraries (pmdarima, fastapi, etc.)
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of your thesis code into the /app folder
COPY . .

# 6. Expose port 8080 (Google Cloud Run requires this specific port)
EXPOSE 8080

# 7. The exact terminal command to start your FastAPI server
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]