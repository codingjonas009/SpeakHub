import discord
from discord.ext import commands
from discord import ui, app_commands
import sqlite3
import asyncio
import os
import json
from typing import Optional, List, Dict
import logging
import time
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_system")

CONFIG_PATH = "voicesystem_config.json"

DEFAULT_CONFIG = {
    "create_voice_channel_id": 1350793212696072276,
    "voice_category_id": 1350793002754375750,
    "db_path": "voicesystem.db",
    "cooldown_time": 5,
    "default_channel_prefix": "🔊╏ ",
    "default_user_permissions": {
        "connect": True,
        "manage_channels": True,
        "move_members": True,
        "mute_members": True
    },
    "interface": {
        "title": "🎙️ Voice Channel Control",
        "description": "Manage your own voice channel",
        "color": "blurple",
        "functions": [
            {"name": "Limit", "description": "Set maximum number of members", "emoji": "<:limit:1353419528524136549>"},
            {"name": "Kick", "description": "Remove members from the channel", "emoji": "<:kick:1353419480671322112>"},
            {"name": "Lock", "description": "Block/unblock channel", "emoji": "<:lock:1353419509515685990>"},
            {"name": "Invite", "description": "Invite users to your channel", "emoji": "<:invite:1353419542830911580>"},
            {"name": "Transfer", "description": "Transfer ownership rights", "emoji": "<:Group73:1353419615681773588>"},
            {"name": "Name", "description": "Rename channel", "emoji": "<:name_edit:1353416939132817568>"},
            {"name": "Block", "description": "Block/unblock users", "emoji": "<:Block:1353419568198058089>"}
        ],
        "footer": "Your channel will be automatically deleted when you leave"
    }
}


def load_config():

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                merged_config = DEFAULT_CONFIG.copy()
                for key, value in config.items():
                    if isinstance(value, dict) and key in merged_config and isinstance(merged_config[key], dict):
                        merged_config[key].update(value)
                    else:
                        merged_config[key] = value
                return merged_config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            return DEFAULT_CONFIG
    else:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        return DEFAULT_CONFIG


class VoiceManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_channels = {}
        self.cooldowns = {}
        self.db_conn = None
        self.config = load_config()
        self.invite_cooldown_duration = timedelta(hours=2)
        self.setup_database()

    def setup_database(self):
        try:
            db_path = self.config["db_path"]
            if not os.path.exists(db_path):
                logger.info("Creating new database for Voice System")

            self.db_conn = sqlite3.connect(db_path)
            cursor = self.db_conn.cursor()

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS voice_channels (
                channel_id INTEGER PRIMARY KEY,
                owner_id INTEGER,
                interface_message_id INTEGER DEFAULT NULL
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_users (
                channel_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (channel_id, user_id)
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id INTEGER NOT NULL,
                invited_user_id INTEGER NOT NULL,
                invited_at TEXT NOT NULL,
                channel_id INTEGER,
                UNIQUE(inviter_id, invited_user_id, channel_id)
            )
            ''')

            self.db_conn.commit()
            logger.info("Database setup successful")
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
            raise

    def check_invite_cooldown(self, inviter_id, invited_user_id, channel_id=None):
        try:
            cursor = self.db_conn.cursor()
            query = '''
                SELECT invited_at 
                FROM user_invites 
                WHERE inviter_id = ? AND invited_user_id = ?
            '''
            params = [inviter_id, invited_user_id]

            if channel_id is not None:
                query += ' AND channel_id = ?'
                params.append(channel_id)

            query += ' ORDER BY invited_at DESC LIMIT 1'

            cursor.execute(query, params)
            result = cursor.fetchone()

            if result:
                last_invite_time = datetime.fromisoformat(result[0])
                return datetime.now() - last_invite_time < self.invite_cooldown_duration
            return False
        except Exception as e:
            logger.error(f"Error checking invite cooldown: {e}")
            return False

    def save_invite_timestamp(self, inviter_id, invited_user_id, channel_id=None):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO user_invites (inviter_id, invited_user_id, invited_at, channel_id) 
                VALUES (?, ?, ?, ?)
            ''', (inviter_id, invited_user_id, datetime.now().isoformat(), channel_id))
            self.db_conn.commit()
        except Exception as e:
            logger.error(f"Error saving invite timestamp: {e}")

    def cog_unload(self):
        if self.db_conn:
            self.db_conn.close()

    async def load_voice_channels(self):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT channel_id, owner_id FROM voice_channels")
            channels = cursor.fetchall()

            for channel_id, owner_id in channels:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    logger.info(f"Deleting non-existent channel {channel_id} from database")
                    cursor.execute("DELETE FROM voice_channels WHERE channel_id = ?", (channel_id,))
                    cursor.execute("DELETE FROM blocked_users WHERE channel_id = ?", (channel_id,))
                else:
                    self.voice_channels[channel_id] = owner_id

            self.db_conn.commit()
            logger.info(f"{len(self.voice_channels)} active voice channels loaded")
        except Exception as e:
            logger.error(f"Error loading voice channels: {e}")

    @commands.Cog.listener()
    async def on_ready(self):

        await self.load_voice_channels()
        logger.info("Voice Manager is ready")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):

        if member.bot:
            return

        if after.channel and after.channel.id == self.config["create_voice_channel_id"]:
            if self.is_on_cooldown(member.id):
                try:
                    wait = discord.Embed(
                        title="❌ Error",
                        description="You are on cooldown. Please wait before creating a new channel.",
                        color=discord.Color.red()
                    )
                    wait.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                    wait.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
                except:
                    pass
                return

            await self.create_voice_channel(member)

        elif before.channel and before.channel.id in self.voice_channels:
            if self.voice_channels[before.channel.id] == member.id:
                await asyncio.sleep(0.5)

                if member.voice is None or member.voice.channel != before.channel:
                    await self.delete_voice_channel(before.channel)

    def is_on_cooldown(self, user_id):

        cooldown_time = self.config["cooldown_time"]

        if user_id in self.cooldowns:
            current_time = asyncio.get_event_loop().time()
            if current_time - self.cooldowns[user_id] < cooldown_time:
                return True

        self.cooldowns[user_id] = asyncio.get_event_loop().time()
        return False

    async def create_voice_channel(self, member):

        try:
            category = self.bot.get_channel(self.config["voice_category_id"])
            if not category:
                logger.error(f"Category not found")
                return

            prefix = self.config["default_channel_prefix"]
            channel_name = f"{prefix}{member.display_name.lower()}"

            default_perms = self.config["default_user_permissions"]

            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(connect=True),
                member: discord.PermissionOverwrite(**default_perms)
            }

            new_channel = await category.create_voice_channel(
                name=channel_name,
                overwrites=overwrites
            )

            await member.move_to(new_channel)

            cursor = self.db_conn.cursor()
            cursor.execute(
                "INSERT INTO voice_channels (channel_id, owner_id) VALUES (?, ?)",
                (new_channel.id, member.id)
            )
            self.db_conn.commit()

            self.voice_channels[new_channel.id] = member.id

            logger.info(f"New voice channel {new_channel.id} created for {member.display_name}")
        except Exception as e:
            logger.error(f"Error creating voice channel: {e}")

    async def delete_voice_channel(self, channel):

        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT interface_message_id FROM voice_channels WHERE channel_id = ?", (channel.id,))
            result = cursor.fetchone()

            cursor.execute("DELETE FROM voice_channels WHERE channel_id = ?", (channel.id,))
            cursor.execute("DELETE FROM blocked_users WHERE channel_id = ?", (channel.id,))
            self.db_conn.commit()

            if channel.id in self.voice_channels:
                del self.voice_channels[channel.id]

            if result and result[0]:
                interface_msg_id = result[0]
                try:
                    for text_channel in channel.guild.text_channels:
                        try:
                            msg = await text_channel.fetch_message(interface_msg_id)
                            await msg.delete()
                            break
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            continue
                except Exception as e:
                    logger.error(f"Error deleting interface message: {e}")

            await channel.delete()
            logger.info(f"Voice channel {channel.id} deleted")
        except Exception as e:
            logger.error(f"Error deleting voice channel: {e}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setup_interface(self, ctx):

        await self.send_interface(ctx.channel, None, None)
        confirm_embed = discord.Embed(
            title="✅ Success",
            description="Interface has been sent.",
            color=discord.Color.green()
        )
        confirm_embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
        confirm_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
        confirm = await ctx.send(embed=confirm_embed)
        await ctx.message.delete()
        await asyncio.sleep(3)
        await confirm.delete()

    async def send_interface(self, text_channel, voice_channel, owner):

        interface_config = self.config["interface"]

        embed = discord.Embed(
            title="",
            description="""
            <:Interface:1353418678099640320> **Voice Channel Interface**
            This is the universal voice channel interface. You can use this interface to manage your voice channel.
            This Interface can be used only for the Voice channels, that are created by the join to create function.
            The Interface can only be used by the owner of the voice channel.

            """,
            color=self.get_color_from_config(interface_config.get("color", "blurple"))
        )

        embed.add_field(name="Usage",
                        value="This Interface can be used to manage your voice channel. You can set a limit for the maximum number of members, kick members, lock/unlock the channel, invite users, transfer the ownership, rename the channel, and block/unblock users.",
                        inline=False)

        functions_text = ""
        for func in interface_config.get("functions", []):
            functions_text += f"• {func.get('emoji', '')} `{func.get('name', '')}` - {func.get('description', '')}\n"

        embed.add_field(name="Functions",
                        value=functions_text,
                        inline=False)

        embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
        embed.set_thumbnail(
            url="https://cdn.discordapp.com/attachments/1348041801155739747/1353783250472009758/settings.png?ex=67e2e866&is=67e196e6&hm=099ea842b57c1d529490d0ea214ccc54488af9c333950366d68cd735b96bcda7&")
        embed.set_image(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353783627745329182/SpeakHub.png?ex=67e2e8c0&is=67e19740&hm=50351a9d5a83222a197c755d8800a15a14649929b747766d9721f1f96a124373&")
        view = self.VoiceChannelView(self)

        await text_channel.send(embed=embed, view=view)
        logger.info(f"Universal voice interface created in {text_channel.name}")

    def get_color_from_config(self, color_str):

        colors = {
            "red": discord.Color.red(),
            "green": discord.Color.green(),
            "blue": discord.Color.blue(),
            "blurple": discord.Color.blurple(),
            "purple": discord.Color.purple(),
            "orange": discord.Color.orange(),
            "yellow": discord.Color.yellow()
        }
        return colors.get(color_str.lower(), discord.Color.blurple())

    class VoiceChannelView(ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

            for func in cog.config["interface"]["functions"]:
                name = func.get("name", "")
                emoji = func.get("emoji", "")

                if name == "Limit":
                    self.add_item(LimitMembersButton(cog, emoji))
                elif name == "Kick":
                    self.add_item(KickMemberButton(cog, emoji))
                elif name == "Lock":
                    self.add_item(LockChannelButton(cog, emoji))
                elif name == "Invite":
                    self.add_item(InviteUserButton(cog, emoji))
                elif name == "Transfer":
                    self.add_item(TransferOwnerButton(cog, emoji))
                elif name == "Name":
                    self.add_item(RenameChannelButton(cog, emoji))
                elif name == "Block":
                    self.add_item(BlockUserButton(cog, emoji))

    async def is_channel_owner(self, user_id, channel_id):

        return channel_id in self.voice_channels and self.voice_channels[channel_id] == user_id

    async def get_blocked_users(self, channel_id):

        cursor = self.db_conn.cursor()
        cursor.execute("SELECT user_id FROM blocked_users WHERE channel_id = ?", (channel_id,))
        return [row[0] for row in cursor.fetchall()]

    async def block_user(self, channel_id, user_id):

        cursor = self.db_conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO blocked_users (channel_id, user_id) VALUES (?, ?)",
            (channel_id, user_id)
        )
        self.db_conn.commit()

        channel = self.bot.get_channel(channel_id)
        if channel:
            member = channel.guild.get_member(user_id)
            if member:
                await channel.set_permissions(member, connect=False)
                if member.voice and member.voice.channel and member.voice.channel.id == channel_id:
                    await member.move_to(None)

    async def unblock_user(self, channel_id, user_id):

        cursor = self.db_conn.cursor()
        cursor.execute(
            "DELETE FROM blocked_users WHERE channel_id = ? AND user_id = ?",
            (channel_id, user_id)
        )
        self.db_conn.commit()

        channel = self.bot.get_channel(channel_id)
        if channel:
            member = channel.guild.get_member(user_id)
            if member:
                await channel.set_permissions(member, connect=True)


class VoiceChannelView(ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

        for func in cog.config["interface"]["functions"]:
            name = func.get("name", "")
            emoji = func.get("emoji", "")

            if name == "Limit":
                self.add_item(LimitMembersButton(cog, emoji))
            elif name == "Kick":
                self.add_item(KickMemberButton(cog, emoji))
            elif name == "Lock":
                self.add_item(LockChannelButton(cog, emoji))
            elif name == "Invite":
                self.add_item(InviteUserButton(cog, emoji))
            elif name == "Transfer":
                self.add_item(TransferOwnerButton(cog, emoji))
            elif name == "Name":
                self.add_item(RenameChannelButton(cog, emoji))
            elif name == "Block":
                self.add_item(BlockUserButton(cog, emoji))


class VoiceChannelButton(ui.Button):
    def __init__(self, cog, label, emoji, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"voice_{label.lower()}")
        self.cog = cog

    async def callback(self, interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            embed = discord.Embed(
                title="❌ Error",
                description="You must be in a voice channel to use this feature.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        channel_id = interaction.user.voice.channel.id

        if channel_id not in self.cog.voice_channels:
            embed = discord.Embed(
                title="❌ Error",
                description="This voice channel is not managed by the Voice Manager.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        if not await self.cog.is_channel_owner(interaction.user.id, channel_id):
            embed = discord.Embed(
                title="❌ Error",
                description="You are not the owner of this voice channel.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            embed = discord.Embed(
                title="❌ Error",
                description="This voice channel does not exist anymore.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        return channel_id


class LimitMembersButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:limit:1353109115618197575>"
        super().__init__(cog, "Limit", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        modal = LimitMembersModal(self.cog, channel_id)
        await interaction.response.send_modal(modal)


class KickMemberButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:remove:1353109143421980793>"
        super().__init__(cog, "Kick", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        channel = interaction.guild.get_channel(channel_id)
        members = [m for m in channel.members if m.id != interaction.user.id]

        if not members:
            embed = discord.Embed(
                title="❌ Information",
                description="There are no other members in your channel.",
                color=discord.Color.orange()
            )

            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = MemberSelectView(self.cog, channel_id, members, "kick")
        select_member_to_kick = discord.Embed(
            title="Select Member",
            description="Select a member to kick from the dropdown menu below",
            color=discord.Color.blurple()
        )
        select_member_to_kick.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
        await interaction.response.send_message(embed=select_member_to_kick, view=view, ephemeral=True)


class LockChannelButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:lock:1353109128901427281>"
        super().__init__(cog, "Lock", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        channel = interaction.guild.get_channel(channel_id)
        current_state = channel.overwrites_for(interaction.guild.default_role).connect

        new_state = None if current_state is False else False
        await channel.set_permissions(interaction.guild.default_role, connect=new_state)

        status = "locked" if new_state is False else "unlocked"
        embed = discord.Embed(
            title="✅ Success",
            description=f"Your channel has been {status}.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
        embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InviteUserButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:invite:1353109103865499648>"
        super().__init__(cog, "Invite", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        modal = InviteUserModal(self.cog, channel_id)
        await interaction.response.send_modal(modal)


class TransferOwnerButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:Group73:1353109060869685488>"
        super().__init__(cog, "Transfer", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        channel = interaction.guild.get_channel(channel_id)
        members = [m for m in channel.members if m.id != interaction.user.id]

        if not members:
            embed = discord.Embed(
                title="❌ Information",
                description="There are no other members in your channel.",
                color=discord.Color.orange()
            )
            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            embed.set_thumbnail(
                url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e193f8&is=67e04278&hm=57f2736b6cb9147cff88fe3fdd2dd61f7eff63ffb89bff8578660f9619f27405&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = MemberSelectView(self.cog, channel_id, members, "transfer")
        new_owner = discord.embeds.Embed(
            title="New Owner",
            description="Select a new owner in the dropdown menu below",
            color=discord.Color.blurple()
        )
        new_owner.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
        new_owner.set_thumbnail(
            url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417523202097303/Group_73.png?ex=67e193ca&is=67e0424a&hm=3e8c633e8a2efcbaa1de986ad2e7da68883e58704e547f60675595cab9b830e1&")
        await interaction.response.send_message(embed=new_owner, view=view, ephemeral=True)


class RenameChannelButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:edit:1353109040049160324>"
        super().__init__(cog, "Name", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        modal = RenameChannelModal(self.cog, channel_id)
        await interaction.response.send_modal(modal)


class BlockUserButton(VoiceChannelButton):
    def __init__(self, cog, emoji=None):
        emoji = emoji or "<:Block:1353109180226994307>"
        super().__init__(cog, "Block", emoji)

    async def callback(self, interaction):
        channel_id = await super().callback(interaction)
        if not channel_id:
            return

        blocked_users = await self.cog.get_blocked_users(channel_id)

        view = BlockUserView(self.cog, channel_id, blocked_users)
        manage_blocked_users = discord.Embed(
            title="Manage Blocked Users",
            description="Select a Action",
            color=discord.Color.blurple()
        )
        manage_blocked_users.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")

        await interaction.response.send_message(embed=manage_blocked_users , view=view, ephemeral=True)


class LimitMembersModal(ui.Modal, title="Set Maximum Members"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    limit = ui.TextInput(
        label="Number of members (0 = unlimited)",
        placeholder="Enter a number between 0 and 99",
        min_length=1,
        max_length=2,
        required=True
    )

    async def on_submit(self, interaction):
        try:
            limit_value = int(self.limit.value)
            if limit_value < 0 or limit_value > 99:
                invalid_number = discord.Embed(
                    title="Select a valid number",
                    description="Please enter a number between 0 and 99.",
                    color=discord.Color.red()
                )
                invalid_number.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                return

            channel = interaction.guild.get_channel(self.channel_id)
            if not channel:
                this_voice_channel_no_longer_exists = discord.Embed(
                    title="❌ Error",
                    description="This voice channel no longer exists.",
                    color=discord.Color.red()
                )
                this_voice_channel_no_longer_exists.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
                this_voice_channel_no_longer_exists.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")

                await interaction.response.send_message(embed=this_voice_channel_no_longer_exists, ephemeral=True)
                return

            user_limit = 0 if limit_value == 0 else limit_value
            await channel.edit(user_limit=user_limit)

            if user_limit == 0:
                removed_user_limit = discord.Embed(
                    title="✅ Success",
                    description="The maximum number of members has been removed.",
                    color=discord.Color.green()
                )
                removed_user_limit.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
                removed_user_limit.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=removed_user_limit, ephemeral=True)
            else:
                set_user_limit = discord.Embed(
                    title="✅ Success",
                    description=f"The maximum number of members has been set to {user_limit}.",
                    color=discord.Color.green()
                )
                set_user_limit.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
                set_user_limit.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=set_user_limit, ephemeral=True)

        except ValueError:
            valit_number = discord.Embed(
                title="❌ Error",
                description="Please enter a valid number.",
                color=discord.Color.red()
            )
            valit_number.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            await interaction.response.send_message(embed=valit_number, ephemeral=True)


class InviteUserModal(ui.Modal, title="Invite User"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    user_input = ui.TextInput(
        label="User (ID, Name, or @Mention)",
        placeholder="Enter the name, ID, or @Mention",
        required=True
    )

    async def on_submit(self, interaction):
        if not self.cog.db_conn:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Error",
                    description="Database connection is unavailable.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        try:
            cursor = self.cog.db_conn.cursor()

            user_input = self.user_input.value.strip()

            if user_input.startswith("<@") and user_input.endswith(">"):
                user_input = user_input[2:-1]
                if user_input.startswith("!"):
                    user_input = user_input[1:]

            user = None
            try:
                user_id = int(user_input)
                user = interaction.guild.get_member(user_id)
            except ValueError:
                for member in interaction.guild.members:
                    if (user_input.lower() in member.name.lower() or
                            (member.nick and user_input.lower() in member.nick.lower())):
                        user = member
                        break

            if not user:
                embed = discord.Embed(
                    title="❌ Error",
                    description="The user could not be found.",
                    color=discord.Color.red()
                )
                embed.set_footer(text=" ┃ SpeakHub",
                                 icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if user.id == interaction.user.id:
                embed = discord.Embed(
                    title="❌ Error",
                    description="You cannot invite yourself.",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(
                    url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
                embed.set_footer(text=" ┃ SpeakHub",
                                 icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if self.cog.check_invite_cooldown(interaction.user.id, user.id, self.channel_id):
                embed = discord.Embed(
                    title="❌ Cooldown",
                    description="You can only invite this user once every 2 hours.",
                    color=discord.Color.red()
                )
                embed.set_footer(text=" ┃ SpeakHub",
                                 icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            channel = interaction.guild.get_channel(self.channel_id)
            if not channel:
                embed = discord.Embed(
                    title="❌ Error",
                    description="This voice channel no longer exists.",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(
                    url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
                embed.set_footer(text=" ┃ SpeakHub",
                                 icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            blocked_users = await self.cog.get_blocked_users(self.channel_id)
            if user.id in blocked_users:
                embed = discord.Embed(
                    title="❌ Error",
                    description="This user is blocked. Please unblock them first.",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(
                    url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
                embed.set_footer(text=" ┃ SpeakHub",
                                 icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            await channel.set_permissions(user, connect=True)
            self.cog.save_invite_timestamp(interaction.user.id, user.id, self.channel_id)

            try:
                embed = discord.Embed(
                    title="✅ Invitation Sent",
                    description=f"You have been invited to the voice channel {channel.mention} by {interaction.user.display_name}.",
                    color=discord.Color.green()
                )
                embed.set_thumbnail(
                    url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
                embed.set_footer(text=" ┃ SpeakHub",
                                 icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await user.send(embed=embed)
            except Exception as dm_error:
                logger.warning(f"Could not send DM to user: {dm_error}")

            embed = discord.Embed(
                title="✅ Success",
                description=f"{user.display_name} has been successfully invited to the voice channel {channel.mention}.",
                color=discord.Color.green()
            )
            embed.set_thumbnail(
                url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
            embed.set_footer(text=" ┃ SpeakHub",
                             icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in invite modal: {e}")
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Error",
                    description="An unexpected error occurred.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )


class RenameChannelModal(ui.Modal, title="Rename Channel"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    new_name = ui.TextInput(
        label="New Channel Name",
        placeholder="Enter the new channel name",
        required=True,
        max_length=30
    )

    async def on_submit(self, interaction):
        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            not_found = discord.Embed(
                title="❌ Error",
                description="This voice channel no longer exists.",
                color=discord.Color.red()
            )
            not_found.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            not_found.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            return

        new_name = f"🔊╏ {self.new_name.value}"
        await channel.edit(name=new_name)
        renamed = discord.Embed(
            title="✅ Erfolg",
            description=f"The Channel got renamed to {new_name}",
            color=discord.Color.green()
        )
        renamed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
        renamed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
        await interaction.response.send_message(embed=renamed, ephemeral=True)


class MemberSelectView(ui.View):
    def __init__(self, cog, channel_id, members, action_type):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id
        self.action_type = action_type

        self.member_select = ui.Select(
            placeholder="Select a member",
            options=[
                discord.SelectOption(
                    label=member.display_name,
                    value=str(member.id),
                    description=f"ID: {member.id}"
                ) for member in members[:25]
            ]
        )

        self.member_select.callback = self.select_callback
        self.add_item(self.member_select)

    async def select_callback(self, interaction):
        selected_id = int(self.member_select.values[0])
        channel = interaction.guild.get_channel(self.channel_id)

        if not channel:
            not_found = discord.Embed(
                title="❌ Error",
                description="This voice channel no longer exists.",
                color=discord.Color.red()
            )
            not_found.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            not_found.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            return

        member = interaction.guild.get_member(selected_id)
        if not member:
            not_found = discord.Embed(
                title="❌ Error",
                description="The member could not be found.",
                color=discord.Color.red()
            )
            not_found.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            not_found.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            return

        if self.action_type == "kick":
            if member.voice and member.voice.channel and member.voice.channel.id == self.channel_id:
                await member.move_to(None)
                got_kicked = discord.Embed(
                    title="✅ Success",
                    description=f"{member.display_name} got kicked from the channel.",
                    color=discord.Color.green()
                )
                got_kicked.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
                got_kicked.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            else:
                embed = discord.Embed(
                    title="❌ Error",
                    description=f"{member.display_name} is not in your channel.",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
                embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
                await interaction.response.send_message(embed=embed, ephemeral=True)

        elif self.action_type == "transfer":
            cursor = self.cog.db_conn.cursor()

            old_owner = interaction.user
            new_owner = member

            await channel.set_permissions(new_owner, connect=True, manage_channels=True, move_members=True,
                                          mute_members=True)

            await channel.set_permissions(old_owner, connect=True)

            cursor.execute(
                "UPDATE voice_channels SET owner_id = ? WHERE channel_id = ?",
                (new_owner.id, self.channel_id)
            )
            self.cog.db_conn.commit()

            self.cog.voice_channels[self.channel_id] = new_owner.id

            transfered = discord.Embed(
                title="✅ Success",
                description=f"{new_owner.display_name} is now the owner of the channel.",
                color=discord.Color.green()
            )
            transfered.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
            transfered.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")

            cursor.execute("SELECT interface_message_id FROM voice_channels WHERE channel_id = ?", (self.channel_id,))
            result = cursor.fetchone()

            if result and result[0]:
                try:
                    for text_channel in interaction.guild.text_channels:
                        try:
                            msg = await text_channel.fetch_message(result[0])
                            embed = msg.embeds[0]

                            for i, field in enumerate(embed.fields):
                                if field.name == "Owner":
                                    embed.set_field_at(i, name="Owner", value=f"<@{new_owner.id}>", inline=False)

                            new_view = VoiceChannelView(self.cog, self.channel_id)
                            await msg.edit(embed=embed, view=new_view)
                            break
                        except:
                            continue
                except Exception as e:
                    logger.error(f"Fehler beim Aktualisieren des Interfaces: {e}")


class BlockUserView(ui.View):
    def __init__(self, cog, channel_id, blocked_users):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id
        self.blocked_users = blocked_users

        self.add_item(AddBlockButton(cog, channel_id))

        if blocked_users:
            self.add_item(RemoveBlockButton(cog, channel_id, blocked_users))


class AddBlockButton(ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Block User", style=discord.ButtonStyle.danger, emoji="🚫")
        self.cog = cog
        self.channel_id = channel_id

    async def callback(self, interaction):
        modal = BlockUserModal(self.cog, self.channel_id)
        await interaction.response.send_modal(modal)

class RemoveBlockButton(ui.Button):
    def __init__(self, cog, channel_id, blocked_users):
        super().__init__(label="Blockierung aufheben", style=discord.ButtonStyle.success, emoji="✅")
        self.cog = cog
        self.channel_id = channel_id
        self.blocked_users = blocked_users

    async def callback(self, interaction):
        if not self.blocked_users:
            no_blocked_users = discord.Embed(
                title="❌ Error",
                description="There are no blocked users in this channel.",
                color=discord.Color.red()
            )
            no_blocked_users.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            no_blocked_users.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            return

        view = UnblockUserView(self.cog, self.channel_id, self.blocked_users)
        unblock_user = discord.Embed(
            title="Select User",
            description="Select a blocked user from the dropdown menu below",
            color=discord.Color.blurple()
        )
        unblock_user.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")

class BlockUserModal(ui.Modal, title="Block User"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    user_input = ui.TextInput(
        label="Benutzer (ID, Name oder @Mention)",
        placeholder="Gib den Namen, die ID oder @Mention ein",
        required=True
    )

    async def on_submit(self, interaction):
        user_input = self.user_input.value.strip()

        if user_input.startswith("<@") and user_input.endswith(">"):
            user_input = user_input[2:-1]
            if user_input.startswith("!"):
                user_input = user_input[1:]

        user = None
        try:
            user_id = int(user_input)
            user = interaction.guild.get_member(user_id)
        except ValueError:
            for member in interaction.guild.members:
                if (user_input.lower() in member.name.lower() or
                        (member.nick and user_input.lower() in member.nick.lower())):
                    user = member
                    break

        if not user:
            embed = discord.Embed(
                title="❌ Error",
                description="Could not find the user.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if user.id == interaction.user.id:
            embed = discord.Embed(
                title="❌ Error",
                description="You cannot block yourself.",
                color=discord.Color.red()
            )

            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417713560588428/Frame_13.png?ex=67e23cb8&is=67e0eb38&hm=6319e48e17178750f92c628339b0295963c457112639313860bdd2abd82c0d7c&")
            embed.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.cog.block_user(self.channel_id, user.id)
        got_blocked = discord.Embed(
            title="✅ Success",
            description=f"{user.display_name} got blocked.",
            color=discord.Color.green()
        )
        got_blocked.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
        got_blocked.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")


class UnblockUserView(ui.View):
    def __init__(self, cog, channel_id, blocked_user_ids):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id

        self.unblock_select = ui.Select(
            placeholder="Select a blocked user",
            options=[]
        )

        guild = cog.bot.get_channel(channel_id).guild
        for user_id in blocked_user_ids[:25]:
            member = guild.get_member(user_id)
            label = f"ID: {user_id}"

            if member:
                label = member.display_name
                description = f"ID: {user_id}"
            else:
                description = "User not found"

            self.unblock_select.options.append(
                discord.SelectOption(
                    label=label,
                    value=str(user_id),
                    description=description
                )
            )

        self.unblock_select.callback = self.select_callback
        self.add_item(self.unblock_select)

    async def select_callback(self, interaction):
        selected_id = int(self.unblock_select.values[0])

        await self.cog.unblock_user(self.channel_id, selected_id)

        member = interaction.guild.get_member(selected_id)
        if member:
            user_name = member.display_name
        else:
            user_name = f"User with ID {selected_id}"

        unblocked = discord.Embed(
            title="✅ Success",
            description=f"{user_name} got unblocked.",
            color=discord.Color.green()
        )
        unblocked.set_thumbnail(url="https://cdn.discordapp.com/attachments/1348041801155739747/1353417842531504249/Frame_40.png?ex=67e23cd6&is=67e0eb56&hm=a6606deaae5d9fec6ade9aba6fd1ae04f9a2636b9bdc3462bd99a986e2966e60&")
        unblocked.set_footer(text=" ┃ SpeakHub", icon_url="https://cdn.discordapp.com/attachments/1348041801155739747/1353778583146991728/interface.png?ex=67e2e40e&is=67e1928e&hm=90008a2ac5dad8426abf1630ad360fd62696f9fc2de04be7c2ad4ef41b96ae87&")


async def setup(bot):
    cog = VoiceManager(bot)
    await bot.add_cog(cog)

    bot.add_view(VoiceManager.VoiceChannelView(cog))

    @bot.event
    async def on_ready():
        if not hasattr(bot, 'voice_views_added'):
            await cog.load_voice_channels()

            for channel_id in cog.voice_channels:
                view = VoiceChannelView(cog, channel_id)
                bot.add_view(view)

            bot.voice_views_added = True
            logger.info("Persistente Voice-Views wurden registriert")
