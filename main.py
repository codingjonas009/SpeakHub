# MAIN
from importlib import import_module

import discord
from discord.ext import commands
import asyncio

import logging
logger = logging.getLogger("discord")
logger.setLevel(logging.WARNING)
logger.setLevel(logging.DEBUG)


intents = discord.Intents.all()
intents.message_content = True

intents.guilds = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents, application_id=123456789)

bot_token = "" # YOUR BOT TOKEN

EXTENSIONS = [
    'cogs.voice',
]

async def load_cogs():
    for ext in EXTENSIONS:
        try:
            if ext not in bot.extensions:
                await bot.load_extension(ext)
                print(f"Erweiterung {ext} erfolgreich geladen.")
                print(f"Geladene Commands: {[command.name for command in bot.commands]}")  # DEBUG
        except Exception as e:
            print(f"Fehler beim Laden der Erweiterung {ext}: {e}")



@bot.event
async def on_ready():
    print(f"Bot ist bereit! Eingeloggt als {bot.user}.")

    try:
        await bot.tree.sync()
        print("Slash-Commands erfolgreich synchronisiert!")
    except Exception as e:
        print(f"Fehler beim Synchronisieren der Commands: {e}")




async def main():
    try:
        async with bot:
            await load_cogs()
            await bot.start("" + bot_token)

    except KeyboardInterrupt:
        print("Bot wird heruntergefahren...")
    except Exception as e:
        print(f"Unerwarteter Fehler: {e}")
    finally:
        await bot.close()
        print("Bot wurde sauber beendet.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Script wurde beendet.")
    except Exception as e:
        print(f"Unerwarteter Fehler im Hauptskript: {e}")
