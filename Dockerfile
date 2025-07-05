# Use an official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system packages if needed 
RUN apt-get update && apt-get install -y gcc

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy all source code
COPY . .

# Set port (App Runner uses 8080)
EXPOSE 8080

# Run the app (update wsgi/app if needed)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "run:app"]
