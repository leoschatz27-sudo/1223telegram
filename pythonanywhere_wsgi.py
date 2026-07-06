import sys
import os

project_home = '/home/cbc'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from pythonanywhere_app import app as application
