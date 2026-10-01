import os
from django.shortcuts import render
def index(request):
  # п.13: читаем секрет FIREBASE_API_KEY из Replit Secrets (переменная окружения)
  firebase_api_key = os.environ.get("FIREBASE_API_KEY", "секрет не найден")
  return render(request, "index.html", {"firebase_api_key": firebase_api_key})
# Create your views here.
