# 💅 AI Nail Salon Assistant

An AI-powered Telegram assistant that automates appointment booking and customer communication for a nail salon.

Built with **Python, OpenAI API, Telegram Bot API, and SQLite**.

## 🚀 Features

- 🤖 AI-powered conversations with customers
- 📅 Appointment booking through natural language
- 🕒 Real-time availability checks
- ✅ Booking confirmation before saving
- ❌ Appointment cancellation
- 🔒 Prevention of overlapping appointments
- 🌏 Vietnam timezone support (UTC+7)
- 💾 SQLite database for appointment storage
- 👩‍💼 Admin command to view bookings

## 🛠 Tech Stack

- **Python** — application logic
- **OpenAI API** — AI conversations and tool calling
- **python-telegram-bot** — Telegram integration
- **SQLite** — appointment storage
- **Git & GitHub** — version control

## 💬 Example Conversation

**Customer:** What times are available tomorrow?

**AI Assistant:**
Available appointments for tomorrow:

- 09:00
- 12:00
- 13:30

Which time works best for you?

**Customer:** Book a manicure at 12:00. My name is Alex.

**AI Assistant:** Please confirm your appointment.

**Customer:** Confirm.

**AI Assistant:** Your appointment has been booked successfully!

## ⚙️ How It Works

1. A customer sends a message to the Telegram bot.
2. OpenAI interprets the customer's request.
3. Python checks appointment availability in SQLite.
4. The customer confirms the appointment.
5. The booking is saved to the database.

## 🔐 Security

API credentials are loaded from environment variables.

Customer databases, local environment files, and Python cache files are excluded from Git using `.gitignore`.

## 📌 Project Status

**Working prototype** — core booking, availability, and cancellation flows have been tested.

## 👨‍💻 Purpose

This project demonstrates practical AI automation for small businesses, combining conversational AI with real-world business workflows.