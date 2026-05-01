# Use a lightweight Python base image
FROM python:3.11-slim

# Install system dependencies required by the .sh script
RUN apt-get update && apt-get install -y \
    curl \
    jq \
    vim \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files
COPY . .

# Ensure the bash script has execution permissions
RUN chmod +x invScrape.sh

# Expose the port your Flask app runs on
EXPOSE 5000

# Start the application
# CMD ["python", "app.py"]
CMD ["python", "-u", "app.py"]