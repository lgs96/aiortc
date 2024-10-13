#!/bin/bash

# Create a new conda environment with Python 3.12.2
conda create -n apprtc_env python=3.12.2 -y

# Activate the new environment
conda activate apprtc_env

# Install packages
pip install aiohttp==3.10.5
pip install aiortc==1.8.0
pip install websockets==12.0
pip install opencv-python-headless==4.9.0.80
pip install av==11.0.0  # Required by aiortc for media handling
pip install numpy==1.26.4  # Required by opencv
pip install aioice==0.9.0  # Required by aiortc for ICE support

# Optional: Install additional packages if needed
pip install matplotlib==3.8.4
pip install pandas==2.2.2

# Verify installation
python -c "import aiohttp, aiortc, websockets, cv2, av, numpy, aioice; print('All packages imported successfully')"

echo "Environment setup complete. You can now run your AppRTC script."