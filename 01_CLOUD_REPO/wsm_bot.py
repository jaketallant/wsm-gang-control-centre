"""WEST STAINS MASSIVE | interactive FiveM Discord management bot.

This program NEVER creates, renames, deletes or changes permission overwrites
on existing guild channels or roles. It posts panels only when the owner runs
/wsm_install; other changes happen only upon staff/member commands.
"""
from __future__ import annotations
import asyncio
import getpass
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from storage import Store
from cards import card_png

ROOT=Path(__file__).resolve().parent
GOLD=0xFFD21E
TZ=ZoneInfo('Europe/London')
RANKS=['🔰 Prospect','🟡 WSM Member','💀 Enforcer','🏆 OG','⚡ Shot Caller','💛 Underboss','👑 WSM Boss']
LEADERS=set(RANKS[4:])
AWARDS={
    'first_shift': ('First Shift', 'Clocked in for the first time.'),
    'regular': ('On The Block', 'Five separate gang sessions.'),
    'two_hours': ('Night Owl', 'Two hours of total recorded gang activity.'),
    'first_mission': ('Street Rookie', 'Completed a WSM RP mission.'),
    'five_missions': ('Block Veteran', 'Completed five RP missions.'),
    'meeting': ('At The Table', 'RSVPed to a gang meeting.'),
    'prospect': ('Welcome To The Block', 'Application accepted by High Command.'),
    'shadow': ('The Shadow', 'Secret award given by High Command.'),
    'legend': ('WSM Legend', 'Secret award given by the Boss.'),
}


def embed(title, description='', **kw):
    e=discord.Embed(title=title,description=description,colour=GOLD,**kw)
    e.set_footer(text='WEST STAINS MASSIVE  •  ONE BLOCK, ONE FAMILY')
    return e


def member_rank(member):
    for name in reversed(RANKS):
        if any(r.name==name for r in member.roles): return name
    return 'Visitor / Ally'


def rank_index(member):
    for k in range(len(RANKS)-1,-1,-1):
        if any(r.name==RANKS[k] for r in member.roles): return k
    return -1


def staff(interaction):
    if not interaction.guild or not isinstance(interaction.user,discord.Member): return False
    return interaction.user.id==interaction.guild.owner_id or any(r.name in LEADERS for r in interaction.user.roles)


def commander(interaction):
    if not staff(interaction): return False
    return interaction.user.id==interaction.guild.owner_id or any(r.name in RANKS[5:] for r in interaction.user.roles)


def wsm_only(interaction, bot):
    return bool(interaction.guild and interaction.guild.id == bot.target_guild_id)


async def deny(interaction, why='Only WSM High Command can do that.'):
    if interaction.response.is_done(): await interaction.followup.send('⛔ '+why,ephemeral=True)
    else: await interaction.response.send_message('⛔ '+why,ephemeral=True)


def channel_named(guild, name):
    return discord.utils.get(guild.text_channels,name=name)


async def private_log(bot, guild, actor, action, target=None, note=''):
    bot.store.audit(guild.id,actor,action,target,note)
    ch=channel_named(guild,'📑・private-logs')
    if ch is not None:
        try:
            await ch.send(embed=embed('WSM • PRIVATE AUDIT',f'**Action:** {action}\n**By:** <@{actor}>\n**Target:** {f"<@{target}>" if target else "—"}\n**Details:** {note[:900]}'),
                          allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden,discord.HTTPException): pass


def get_guild_member(guild, user_id):
    return guild.get_member(user_id)


async def fetch_member(guild, user_id):
    cached=get_guild_member(guild,user_id)
    if cached: return cached
    try: return await guild.fetch_member(user_id)
    except (discord.NotFound,discord.Forbidden,discord.HTTPException): return None


def is_wsm(member): return rank_index(member)>=0


def roster_embed(bot, guild):
    e=embed('🟡 WSM • LIVE GANG ROSTER','Current Discord gang ranks. Uses **Server Members Intent**.')
    counted=0
    for name in reversed(RANKS):
        r=discord.utils.get(guild.roles,name=name)
        if not r: continue
        members=[m for m in r.members if rank_index(m)==RANKS.index(name) and not m.bot]
        counted+=len(members)
        labels=[discord.utils.escape_markdown(m.display_name)[:48] for m in members[:14]]
        if len(members)>14: labels.append(f'… plus {len(members)-14} more')
        e.add_field(name=f'{name} — {len(members)}',value='\n'.join(labels)[:1024] if labels else '*Nobody assigned*',inline=False)
    e.description=f'**{counted}** ranked members • Updated <t:{int(time.time())}:R>\nMembers may appear missing if the Members Intent is disabled.'
    return e


def score_embed(bot, guild):
    e=embed('🏆 WSM • ACTIVITY LEADERBOARD', 'Optional RP sessions and completed mission points — not an automatic measure of real FiveM playtime.')
    lines=[]
    rows=bot.store.all('''SELECT user_id, points FROM points WHERE guild_id=? ORDER BY points DESC LIMIT 30''',(guild.id,))
    ids={r['user_id'] for r in rows}
    active=bot.store.all('SELECT DISTINCT user_id FROM activity_sessions WHERE guild_id=?',(guild.id,))
    ids.update(r['user_id'] for r in active)
    ranking=[]
    for user_id in ids:
        m=guild.get_member(user_id)
        if m and is_wsm(m):
            ranking.append((user_id,bot.store.score(guild.id,user_id),bot.store.seconds(guild.id,user_id)))
    ranking.sort(key=lambda x:(x[1],x[2]),reverse=True)
    for ix,(uid,points,seconds) in enumerate(ranking[:15],1):
        lines.append(f'**{ix}.** <@{uid}> — **{points}** pts • {seconds/3600:.1f}h')
    e.description='\n'.join(lines) if lines else 'No logged activity yet. Use the buttons in #activity-check to start.'
    return e


def meetings_embed(bot,guild):
    now=int(time.time())
    rows=bot.store.all("SELECT * FROM meetings WHERE guild_id=? AND status='open' AND starts_at>? ORDER BY starts_at LIMIT 12",(guild.id,now))
    content=[f'**#{r["id"]}** • {discord.utils.escape_markdown(r["title"])} • <t:{r["starts_at"]}:F>' for r in rows]
    return embed('📅 WSM • UPCOMING MEETINGS','\n'.join(content) if content else 'No upcoming meetings. Leadership can use `/wsm_meeting_create`.')


def missions_embed(bot,guild):
    rows=bot.store.all("SELECT * FROM missions WHERE guild_id=? AND status='open' ORDER BY id DESC LIMIT 12",(guild.id,))
    content=[f'**#{r["id"]}** • {discord.utils.escape_markdown(r["title"])} • **{r["points"]} pts**' for r in rows]
    return embed('🗺️ WSM • RP MISSION BOARD','\n'.join(content) if content else 'No open RP missions. Leadership can use `/wsm_mission_create`.')


async def check_achievements(bot, guild_id, user_id):
    st=bot.store
    if st.attendance(guild_id,user_id)>=1: st.add_achievement(guild_id,user_id,'first_shift')
    if st.attendance(guild_id,user_id)>=5: st.add_achievement(guild_id,user_id,'regular')
    if st.seconds(guild_id,user_id)>=7200: st.add_achievement(guild_id,user_id,'two_hours')
    n=st.one('''SELECT COUNT(*) AS c FROM mission_members mm JOIN missions m ON mm.mission_id=m.id
      WHERE m.guild_id=? AND mm.user_id=? AND mm.completed=1''',(guild_id,user_id))['c']
    if n>=1: st.add_achievement(guild_id,user_id,'first_mission')
    if n>=5: st.add_achievement(guild_id,user_id,'five_missions')


class RecruitModal(discord.ui.Modal,title='Apply to WEST STAINS MASSIVE'):
    character=discord.ui.TextInput(label='In-city character name',max_length=60)
    experience=discord.ui.TextInput(label='FiveM RP experience',style=discord.TextStyle.paragraph,max_length=350)
    reason=discord.ui.TextInput(label='Why WSM and what can you bring?',style=discord.TextStyle.paragraph,max_length=500)
    availability=discord.ui.TextInput(label='Usual active days/times',max_length=100)
    rules=discord.ui.TextInput(label='Agree to WSM and city RP rules? (yes/no)',max_length=20)

    def __init__(self,bot):
        super().__init__(); self.bot=bot

    async def on_submit(self,i):
        bot=self.bot
        if not wsm_only(i,bot): return await deny(i,'This panel is for the WSM server.')
        if isinstance(i.user,discord.Member) and rank_index(i.user)>=0:
            return await deny(i,'You already have a WSM gang rank. Speak with High Command instead.')
        s=bot.store
        prior=s.one("SELECT id FROM applications WHERE guild_id=? AND user_id=? AND status='pending'",(i.guild.id,i.user.id))
        if prior: return await deny(i,f'Your application #{prior["id"]} is already awaiting review.')
        review=channel_named(i.guild,'📋・recruit-reviews') or channel_named(i.guild,'📩・applications')
        if not review: return await deny(i,'Recruitment review channel is missing. Ask the Boss to check the existing WSM channels.')
        if str(self.rules.value).strip().lower() not in ('yes','y','i agree','agree'):
            return await deny(i,'You must agree to the RP rules to apply.')
        ans={'character':str(self.character.value),'experience':str(self.experience.value),
             'reason':str(self.reason.value),'availability':str(self.availability.value)}
        await i.response.defer(ephemeral=True)
        row=s.execute('INSERT INTO applications(guild_id,user_id,answers,created_at) VALUES(?,?,?,?)',
               (i.guild.id,i.user.id,json.dumps(ans,ensure_ascii=False),int(time.time())))
        app_id=row.lastrowid
        e=embed(f'📝 WSM APPLICATION #{app_id}',f'**Applicant:** <@{i.user.id}>\n**In-city name:** {discord.utils.escape_markdown(ans["character"])}')
        for title,key in [('RP Experience','experience'),('Why WSM?','reason'),('Availability','availability')]:
            e.add_field(name=title,value=discord.utils.escape_mentions(ans[key])[:1000],inline=False)
        try:
            msg=await review.send(embed=e,view=ReviewView(bot,app_id),allowed_mentions=discord.AllowedMentions.none())
            s.execute('UPDATE applications SET review_message_id=? WHERE id=?',(msg.id,app_id))
            s.set_profile(i.guild.id,i.user.id,ans['character'])
            await i.followup.send(f'✅ Application **#{app_id}** sent privately to WSM leadership.',ephemeral=True)
        except discord.Forbidden:
            s.execute('DELETE FROM applications WHERE id=?',(app_id,))
            await i.followup.send('⚠️ Discord denied access to the private review room. Give this bot View Channel, Send Messages and Embed Links there.',ephemeral=True)


class RecruitPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot

    @discord.ui.button(label='Apply to WSM',emoji='📝',style=discord.ButtonStyle.primary,custom_id='wsm:panel:apply')
    async def apply(self,i,button):
        if not wsm_only(i,self.bot): return await deny(i)
        await i.response.send_modal(RecruitModal(self.bot))


class ReviewView(discord.ui.View):
    def __init__(self,bot,app_id):
        super().__init__(timeout=None); self.bot=bot; self.app_id=app_id
        self.add_item(ReviewButton(bot,app_id,'accept'))
        self.add_item(ReviewButton(bot,app_id,'reject'))


class ReviewButton(discord.ui.Button):
    def __init__(self,bot,app_id,decision):
        super().__init__(label='Accept + Prospect' if decision=='accept' else 'Reject',
                         emoji='✅' if decision=='accept' else '❌',
                         style=discord.ButtonStyle.success if decision=='accept' else discord.ButtonStyle.danger,
                         custom_id=f'wsm:review:{decision}:{app_id}')
        self.bot=bot; self.app_id=app_id; self.decision=decision

    async def callback(self,i):
        bot=self.bot
        if not wsm_only(i,bot) or not staff(i): return await deny(i)
        s=bot.store; row=s.one('SELECT * FROM applications WHERE id=? AND guild_id=?',(self.app_id,i.guild.id))
        if not row or row['status']!='pending': return await deny(i,'This application has already been reviewed.')
        member=await fetch_member(i.guild,row['user_id'])
        if self.decision=='accept':
            if member is None: return await deny(i,'Applicant has left this Discord server.')
            role=discord.utils.get(i.guild.roles,name='🔰 Prospect')
            if role is None: return await deny(i,'Existing 🔰 Prospect role not found. No new roles will be created.')
            me=i.guild.me or await fetch_member(i.guild,bot.user.id)
            if not me or not me.guild_permissions.manage_roles or me.top_role<=role:
                return await deny(i,'Bot cannot assign Prospect. In Server Settings → Roles, move the bot role ABOVE 🔰 Prospect and allow Manage Roles.')
            if rank_index(member)>=0:
                return await deny(i,'Applicant already has a WSM gang rank. Review manually instead.')
            await i.response.defer(ephemeral=True)
            try: await member.add_roles(role,reason=f'WSM recruitment #{self.app_id} approved by {i.user.id}')
            except discord.Forbidden: return await deny(i,'Discord refused the Prospect role assignment. Check bot role position.')
            s.add_achievement(i.guild.id,row['user_id'],'prospect')
        else:
            await i.response.defer(ephemeral=True)
        s.execute('UPDATE applications SET status=?,reviewer_id=?,reviewed_at=? WHERE id=?',
                  ('accepted' if self.decision=='accept' else 'rejected',i.user.id,int(time.time()),self.app_id))
        e=i.message.embeds[0] if i.message.embeds else embed(f'WSM APPLICATION #{self.app_id}')
        e.add_field(name='Decision',value=f'**{self.decision.upper()}** by <@{i.user.id}>',inline=False)
        try: await i.message.edit(embed=e,view=None)
        except discord.HTTPException: pass
        await private_log(bot,i.guild,i.user.id,'recruit-'+self.decision,row['user_id'],f'Application #{self.app_id}')
        await i.followup.send(f'Application #{self.app_id} **{self.decision}ed**.',ephemeral=True)
        if member:
            try: await member.send(f'Your West Stains Massive application #{self.app_id} was {self.decision}ed.' if self.decision!='accept' else
                                    f'Welcome to West Stains Massive! Your application #{self.app_id} was accepted. You are now a Prospect.')
            except (discord.Forbidden,discord.HTTPException): pass


class RosterPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None);self.bot=bot
    @discord.ui.button(label='View live roster',emoji='👥',style=discord.ButtonStyle.primary,custom_id='wsm:panel:roster')
    async def roster(self,i,button):
        if not wsm_only(i,self.bot): return await deny(i)
        await i.response.send_message(embed=roster_embed(self.bot,i.guild),ephemeral=True)
    @discord.ui.button(label='My ID card',emoji='🪪',style=discord.ButtonStyle.secondary,custom_id='wsm:panel:card')
    async def card(self,i,button):
        if not wsm_only(i,self.bot): return await deny(i)
        await send_card(self.bot,i,i.user)


class ActivityPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None);self.bot=bot
    @discord.ui.button(label='Clock In',emoji='🟢',style=discord.ButtonStyle.success,custom_id='wsm:panel:clockin')
    async def clockin(self,i,button): await clock_action(self.bot,i,True)
    @discord.ui.button(label='Clock Out',emoji='🟡',style=discord.ButtonStyle.secondary,custom_id='wsm:panel:clockout')
    async def clockout(self,i,button): await clock_action(self.bot,i,False)
    @discord.ui.button(label='Leaderboard',emoji='🏆',style=discord.ButtonStyle.primary,custom_id='wsm:panel:leaderboard')
    async def leader(self,i,button):
        if not wsm_only(i,self.bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'The leaderboard is for full WSM members only.')
        await i.response.send_message(embed=score_embed(self.bot,i.guild),ephemeral=True)


async def clock_action(bot,i,starting):
    if not wsm_only(i,bot) or not isinstance(i.user,discord.Member) or not is_wsm(i.user):
        return await deny(i,'Only current WSM gang ranks can clock in or out.')
    success=bot.store.clockin(i.guild.id,i.user.id) if starting else bot.store.clockout(i.guild.id,i.user.id)
    if success: await check_achievements(bot,i.guild.id,i.user.id)
    await i.response.send_message(('✅ Shift started.' if starting else '✅ Shift ended.') if success else
                                  ('Already clocked in.' if starting else 'You have no active shift.'),ephemeral=True)


class MeetingsPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None);self.bot=bot
    @discord.ui.button(label='Upcoming meetings',emoji='📅',style=discord.ButtonStyle.primary,custom_id='wsm:panel:meetings')
    async def meetings(self,i,button):
        if not wsm_only(i,self.bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'Meetings are available to full WSM members only.')
        await i.response.send_message(embed=meetings_embed(self.bot,i.guild),ephemeral=True)


class MeetingView(discord.ui.View):
    def __init__(self,bot,meeting_id):
        super().__init__(timeout=None);self.add_item(RSVPButton(bot,meeting_id,'yes'));self.add_item(RSVPButton(bot,meeting_id,'no'))


class RSVPButton(discord.ui.Button):
    def __init__(self,bot,meeting_id,response):
        super().__init__(label='Going' if response=='yes' else 'Cannot attend',style=discord.ButtonStyle.success if response=='yes' else discord.ButtonStyle.secondary,
                         custom_id=f'wsm:meeting:{response}:{meeting_id}')
        self.bot=bot;self.meeting_id=meeting_id;self.answer=response

    async def callback(self,i):
        if not wsm_only(i,self.bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'Meetings are for WSM members only.')
        rec=self.bot.store.one('SELECT * FROM meetings WHERE id=? AND guild_id=?',(self.meeting_id,i.guild.id))
        if not rec or rec['status']!='open' or rec['starts_at']<=int(time.time()): return await deny(i,'This meeting has ended.')
        self.bot.store.execute('INSERT INTO rsvps VALUES(?,?,?) ON CONFLICT(meeting_id,user_id) DO UPDATE SET response=excluded.response',
                       (self.meeting_id,i.user.id,self.answer))
        if self.answer=='yes': self.bot.store.add_achievement(i.guild.id,i.user.id,'meeting')
        await i.response.send_message(f'RSVP saved: **{"Going" if self.answer=="yes" else "Cannot attend"}** for meeting #{self.meeting_id}.',ephemeral=True)


class MissionsPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None);self.bot=bot
    @discord.ui.button(label='Open RP missions',emoji='🗺️',style=discord.ButtonStyle.primary,custom_id='wsm:panel:missions')
    async def missions(self,i,button):
        if not wsm_only(i,self.bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'RP missions are available to full WSM members only.')
        await i.response.send_message(embed=missions_embed(self.bot,i.guild),ephemeral=True)


class MissionView(discord.ui.View):
    def __init__(self,bot,mission_id):
        super().__init__(timeout=None); self.add_item(MissionButton(bot,mission_id,True)); self.add_item(MissionButton(bot,mission_id,False))


class MissionButton(discord.ui.Button):
    def __init__(self,bot,mission_id,joining):
        super().__init__(label='Join RP mission' if joining else 'Leave mission',
                         style=discord.ButtonStyle.success if joining else discord.ButtonStyle.secondary,
                         custom_id=f'wsm:mission:{"join" if joining else "leave"}:{mission_id}')
        self.bot=bot;self.mission_id=mission_id;self.joining=joining

    async def callback(self,i):
        if not wsm_only(i,self.bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'You must be a full WSM member to join missions.')
        rec=self.bot.store.one('SELECT * FROM missions WHERE id=? AND guild_id=?',(self.mission_id,i.guild.id))
        if not rec or rec['status']!='open': return await deny(i,'Mission is no longer open.')
        if self.joining:
            row=self.bot.store.execute('INSERT OR IGNORE INTO mission_members(mission_id,user_id) VALUES(?,?)',(self.mission_id,i.user.id))
        else:
            row=self.bot.store.execute('DELETE FROM mission_members WHERE mission_id=? AND user_id=? AND completed=0',(self.mission_id,i.user.id))
        await i.response.send_message(('✅ Signed up.' if self.joining else '✅ Removed from the signup list.') if row.rowcount else
                                       ('Already signed up.' if self.joining else 'No open signup to remove.'),ephemeral=True)


async def send_card(bot,i,member):
    if not wsm_only(i,bot): return await deny(i,'WSM server only.')
    if not isinstance(member,discord.Member): return await deny(i)
    await i.response.defer(ephemeral=True)
    s=bot.store
    row=s.one('SELECT * FROM profiles WHERE guild_id=? AND user_id=?',(i.guild.id,member.id))
    awards=s.all('SELECT achievement FROM achievements WHERE guild_id=? AND user_id=?',(i.guild.id,member.id))
    try: avatar=await member.display_avatar.replace(size=256,format='png').read()
    except (discord.HTTPException,discord.Forbidden): avatar=None
    created=datetime.fromtimestamp(row['joined_at'] if row else int(member.joined_at.timestamp()) if member.joined_at else int(time.time()),TZ).strftime('%d %b %Y')
    try:
        f=card_png(member.display_name,row['character_name'] if row else '',member_rank(member),created,
                   s.seconds(i.guild.id,member.id)/3600,s.score(i.guild.id,member.id),len(awards),avatar)
        await i.followup.send(file=discord.File(f,filename='WSM_ID_Card.png'),ephemeral=True)
    except Exception as ex:
        logging.exception('Card failed')
        await i.followup.send(f'Could not render ID card: {type(ex).__name__}',ephemeral=True)


class WSBot(commands.Bot):
    def __init__(self,guild_id:int,db_path:Path):
        intents=discord.Intents.default()
        intents.message_content=False
        intents.members=True # REQUIRED to show full existing gang roster
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none())
        self.target_guild_id=guild_id
        self.store=Store(db_path)
        self.synced=False

    async def setup_hook(self):
        for v in (RecruitPanel(self),RosterPanel(self),ActivityPanel(self),MeetingsPanel(self),MissionsPanel(self)):
            self.add_view(v)
        for r in self.store.all("SELECT id FROM applications WHERE guild_id=? AND status='pending'",(self.target_guild_id,)):
            self.add_view(ReviewView(self,r['id']))
        for r in self.store.all("SELECT id FROM meetings WHERE guild_id=? AND status='open'",(self.target_guild_id,)):
            self.add_view(MeetingView(self,r['id']))
        for r in self.store.all("SELECT id FROM missions WHERE guild_id=? AND status='open'",(self.target_guild_id,)):
            self.add_view(MissionView(self,r['id']))
        guild=discord.Object(id=self.target_guild_id)
        self.tree.copy_global_to(guild=guild)
        synced=await self.tree.sync(guild=guild)
        print(f'Synced {len(synced)} WSM slash commands to server {self.target_guild_id}.')
        self.remind_loop.start()

    async def on_ready(self):
        guild=self.get_guild(self.target_guild_id)
        print(f'✅ WSM management bot ONLINE: {self.user}')
        print(f'Guild: {guild.name if guild else "NOT FOUND — check server ID / bot invitation"}')
        print('Open your WSM Discord. Type /wsm_install as the server owner.')
        print('KEEP THIS WINDOW OPEN for buttons, reminders and commands to work.')

    @tasks.loop(minutes=5)
    async def remind_loop(self):
        now=int(time.time())
        rows=self.store.all("SELECT * FROM meetings WHERE guild_id=? AND status='open' AND remind_sent=0 AND starts_at>=? AND starts_at<=?",
                        (self.target_guild_id,now,now+1800))
        guild=self.get_guild(self.target_guild_id)
        for r in rows:
            if guild:
                ch=guild.get_channel(r['channel_id'])
                if isinstance(ch,discord.TextChannel):
                    yes=self.store.all("SELECT user_id FROM rsvps WHERE meeting_id=? AND response='yes'",(r['id'],))
                    # Avoid @everyone, @here, and excessive user pings. Discord mentions must be opted in by RSVP.
                    users=[discord.Object(id=row['user_id']) for row in yes[:20]]
                    ping=' '.join(f'<@{m.id}>' for m in users)
                    try:
                        await ch.send(content=ping or None,
                          embed=embed('⏰ WSM MEETING REMINDER',f'**{r["title"]}** begins <t:{r["starts_at"]}:R>.'),
                          allowed_mentions=discord.AllowedMentions(users=users,roles=False,everyone=False))
                    except (discord.Forbidden,discord.HTTPException): logging.exception('Meeting reminder delivery failed')
            self.store.execute('UPDATE meetings SET remind_sent=1 WHERE id=?',(r['id'],))

    @remind_loop.before_loop
    async def before_remind(self): await self.wait_until_ready()


async def install_panel(bot,guild,key,channel_name,message,view):
    ch=channel_named(guild,channel_name)
    if ch is None: return f'⚠ Missing existing #{channel_name} (skipped)'
    try:
        previous=bot.store.one('SELECT * FROM panel_posts WHERE guild_id=? AND name=?',(guild.id,key))
        if previous and previous['channel_id']==ch.id:
            try:
                old=await ch.fetch_message(previous['message_id'])
                if old.author.id==bot.user.id:
                    await old.edit(embed=message,view=view)
                    return f'↻ Updated existing panel in #{ch.name}'
            except (discord.NotFound,discord.Forbidden,discord.HTTPException): pass
        post=await ch.send(embed=message,view=view,allowed_mentions=discord.AllowedMentions.none())
        bot.store.execute('''INSERT INTO panel_posts VALUES(?,?,?,?) ON CONFLICT(guild_id,name)
                     DO UPDATE SET channel_id=excluded.channel_id,message_id=excluded.message_id''',
                     (guild.id,key,ch.id,post.id))
        return f'✅ Created panel in #{ch.name}'
    except discord.Forbidden:
        return f'⛔ No permission in #{ch.name}; allow bot View Channel, Send Messages & Embed Links.'
    except discord.HTTPException as exc:
        return f'⚠ Discord error in #{ch.name}: {str(exc)[:100]}'


def register_commands(bot):
    @bot.tree.command(name='wsm_install',description='Owner-only: post/update WSM panels in EXISTING channels. Never edits server roles/channels.')
    async def wsm_install(i:discord.Interaction):
        if not wsm_only(i,bot) or i.user.id!=i.guild.owner_id: return await deny(i,'Only the server OWNER can install panels.')
        await i.response.defer(ephemeral=True,thinking=True)
        items=[
            ('recruit','📝・apply-to-join',embed('📝 APPLY TO WEST STAINS MASSIVE','Click **Apply to WSM** to submit your character details privately to WSM leadership. Successful applicants are assigned the **existing 🔰 Prospect role**.'),RecruitPanel(bot)),
            ('roster','📋・gang-roster',embed('👥 WSM LIVE ROSTER & ID CARDS','View current ranks or generate your own personalised WSM PNG ID card.'),RosterPanel(bot)),
            ('activity','📊・activity-check',embed('🟡 WSM ACTIVITY HUB','Clock in/out when participating in FiveM roleplay. Activity hours are **self-reported**; the bot does not connect directly to FiveM.'),ActivityPanel(bot)),
            ('meetings','📝・gang-meetings',embed('📅 WSM MEETINGS','Leadership can schedule meetings with `/wsm_meeting_create`. Click below for upcoming meetings.'),MeetingsPanel(bot)),
            ('missions','📍・territories',embed('🗺️ WSM RP MISSION BOARD','Join fictional gang storylines and events. No real-life activities. Leadership can use `/wsm_mission_create`.'),MissionsPanel(bot)),
        ]
        results=[]
        for args in items: results.append(await install_panel(bot,i.guild,*args))
        await private_log(bot,i.guild,i.user.id,'install-panels',note='; '.join(results))
        await i.followup.send('\n'.join(results)+'\n\nNo existing channels, ranks or permissions were changed. Keep the bot window open.',ephemeral=True)

    @bot.tree.command(name='wsm_roster',description='See the current WSM ranked members.')
    async def wsm_roster(i:discord.Interaction):
        if not wsm_only(i,bot): return await deny(i)
        await i.response.send_message(embed=roster_embed(bot,i.guild),ephemeral=True)

    @bot.tree.command(name='wsm_profile',description='Set your in-city character name for WSM ID cards.')
    @app_commands.describe(character_name='Your FiveM RP character name (not real-world personal information)')
    async def wsm_profile(i:discord.Interaction,character_name:app_commands.Range[str,2,50]):
        if not wsm_only(i,bot): return await deny(i)
        bot.store.set_profile(i.guild.id,i.user.id,character_name)
        await i.response.send_message(f'✅ WSM character profile updated: **{discord.utils.escape_markdown(character_name)}**',ephemeral=True)

    @bot.tree.command(name='wsm_id',description='Generate a black/yellow PNG WSM member ID card.')
    async def wsm_id(i:discord.Interaction,member:discord.Member|None=None):
        if not wsm_only(i,bot): return await deny(i)
        if member and member.id!=i.user.id and not staff(i): return await deny(i,'Only leadership can view someone else’s member card.')
        await send_card(bot,i,member or i.user)

    @bot.tree.command(name='wsm_promote',description='High Command: promote a member by one existing WSM rank.')
    async def wsm_promote(i:discord.Interaction,member:discord.Member):
        await change_rank(i,member,1)

    @bot.tree.command(name='wsm_demote',description='High Command: demote a member by one existing WSM rank.')
    async def wsm_demote(i:discord.Interaction,member:discord.Member):
        await change_rank(i,member,-1)

    async def change_rank(i,member,delta):
        if not wsm_only(i,bot) or not staff(i): return await deny(i)
        old=rank_index(member)
        if old<0: return await deny(i,'Member has no existing WSM gang rank. Recruit via applications first.')
        new=old+delta
        if new<0 or new>=len(RANKS): return await deny(i,'Already at the lowest/highest WSM rank.')
        if member.id==i.guild.owner_id: return await deny(i,'Server owner rank cannot be changed by this command.')
        if i.user.id!=i.guild.owner_id:
            actor=rank_index(i.user)
            if old>=actor or new>=actor: return await deny(i,'Cannot manage your own rank or a peer/higher rank.')
            if actor==4 and new>2: return await deny(i,'Shot Callers can only promote up to Enforcer.')
            if actor==5 and new>3: return await deny(i,'Underboss can only promote up to OG. Boss handles High Command ranks.')
        old_role=discord.utils.get(i.guild.roles,name=RANKS[old]);new_role=discord.utils.get(i.guild.roles,name=RANKS[new])
        if not new_role or not old_role: return await deny(i,'One of the existing WSM roles is missing. No roles have been created.')
        me=i.guild.me or await fetch_member(i.guild,bot.user.id)
        if not me or not me.guild_permissions.manage_roles or max(old_role.position,new_role.position)>=me.top_role.position:
            return await deny(i,'Bot cannot manage that role. Move its bot role above all WSM ranks and grant Manage Roles.')
        # One member.edit request instead of separate remove/add; preserve any unrelated roles.
        kept=[r for r in member.roles if r.name not in RANKS and not r.is_default()]
        kept.append(new_role)
        await i.response.defer(ephemeral=True)
        try: await member.edit(roles=kept,reason=f'WSM rank change by {i.user.id}')
        except discord.Forbidden: return await deny(i,'Discord rejected the rank update. Check bot role hierarchy.')
        except discord.HTTPException as exc: return await deny(i,f'Discord error: {exc}')
        action='promoted' if delta==1 else 'demoted'
        await private_log(bot,i.guild,i.user.id,action,member.id,f'{RANKS[old]} → {RANKS[new]}')
        await i.followup.send(f'✅ {member.mention} {action}: **{RANKS[old]} → {RANKS[new]}**',ephemeral=True)

    @bot.tree.command(name='wsm_application_status',description='Check your latest WSM application status.')
    async def wsm_application_status(i:discord.Interaction):
        if not wsm_only(i,bot): return await deny(i)
        r=bot.store.one('SELECT * FROM applications WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 1',(i.guild.id,i.user.id))
        await i.response.send_message(f'Application #{r["id"]}: **{r["status"]}**' if r else 'No application submitted yet. Visit #apply-to-join.',ephemeral=True)

    @bot.tree.command(name='wsm_clockin',description='Start an optional self-reported gang RP session.')
    async def wsm_clockin(i:discord.Interaction): await clock_action(bot,i,True)

    @bot.tree.command(name='wsm_clockout',description='End your current gang RP session.')
    async def wsm_clockout(i:discord.Interaction): await clock_action(bot,i,False)

    @bot.tree.command(name='wsm_leaderboard',description='View logged gang participation and mission points.')
    async def wsm_leaderboard(i:discord.Interaction):
        if not wsm_only(i,bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'The activity leaderboard is for full WSM members only.')
        await i.response.send_message(embed=score_embed(bot,i.guild),ephemeral=True)

    @bot.tree.command(name='wsm_meeting_create',description='High Command: create a private gang meeting and RSVP buttons.')
    @app_commands.describe(title='Short meeting title',when_uk='UK time, YYYY-MM-DD HH:MM (24-hour)',details='Agenda (roleplay meeting)')
    async def wsm_meeting_create(i:discord.Interaction,title:app_commands.Range[str,3,100],when_uk:str,details:app_commands.Range[str,2,800]):
        if not wsm_only(i,bot) or not staff(i): return await deny(i)
        try:
            dt=datetime.strptime(when_uk.strip(),'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
            start=int(dt.timestamp())
            if start<int(time.time())+300 or start>int(time.time())+180*86400: raise ValueError('Date outside range')
        except (ValueError,OverflowError): return await deny(i,'Use UK time YYYY-MM-DD HH:MM, between 5 minutes and 180 days from now.')
        ch=channel_named(i.guild,'📝・gang-meetings')
        if ch is None: return await deny(i,'Existing #gang-meetings channel not found. Nothing was created.')
        await i.response.defer(ephemeral=True)
        db=bot.store
        rid=db.execute('INSERT INTO meetings(guild_id,title,description,starts_at,creator_id,channel_id) VALUES(?,?,?,?,?,?)',
                (i.guild.id,title,details,start,i.user.id,ch.id)).lastrowid
        e=embed(f'📅 WSM MEETING #{rid}: {title}',f'**When:** <t:{start}:F> (<t:{start}:R>)\n**By:** {i.user.mention}\n\n{discord.utils.escape_mentions(details)}\n\nPlease RSVP below.')
        try:
            msg=await ch.send(embed=e,view=MeetingView(bot,rid),allowed_mentions=discord.AllowedMentions.none())
            db.execute('UPDATE meetings SET message_id=? WHERE id=?',(msg.id,rid))
            await private_log(bot,i.guild,i.user.id,'create-meeting',note=f'#{rid} {title}')
            await i.followup.send(f'✅ Meeting #{rid} posted in {ch.mention}. Members can RSVP.',ephemeral=True)
        except discord.Forbidden:
            db.execute('DELETE FROM meetings WHERE id=?',(rid,))
            await i.followup.send('Bot cannot post in #gang-meetings. Check its channel permissions.',ephemeral=True)

    @bot.tree.command(name='wsm_meetings',description='Show the upcoming WSM meetings.')
    async def wsm_meetings(i:discord.Interaction):
        if not wsm_only(i,bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'Meetings are available to full WSM members only.')
        await i.response.send_message(embed=meetings_embed(bot,i.guild),ephemeral=True)

    @bot.tree.command(name='wsm_meeting_cancel',description='High Command: cancel a WSM meeting.')
    async def wsm_meeting_cancel(i:discord.Interaction,meeting_id:int):
        if not wsm_only(i,bot) or not staff(i): return await deny(i)
        r=bot.store.one('SELECT * FROM meetings WHERE id=? AND guild_id=?',(meeting_id,i.guild.id))
        if not r or r['status']!='open': return await deny(i,'Meeting not found or already closed.')
        bot.store.execute("UPDATE meetings SET status='cancelled' WHERE id=?",(meeting_id,))
        ch=i.guild.get_channel(r['channel_id'])
        if ch and r['message_id']:
            try:
                msg=await ch.fetch_message(r['message_id']); await msg.edit(view=None,embed=embed(f'🚫 Meeting #{meeting_id} CANCELLED'))
            except discord.HTTPException: pass
        await private_log(bot,i.guild,i.user.id,'cancel-meeting',note=f'#{meeting_id}')
        await i.response.send_message('✅ Meeting cancelled; RSVP buttons disabled.',ephemeral=True)

    @bot.tree.command(name='wsm_mission_create',description='High Command: post a fictional RP mission with sign-up buttons.')
    async def wsm_mission_create(i:discord.Interaction,title:app_commands.Range[str,3,100],details:app_commands.Range[str,5,900],points:app_commands.Range[int,1,100]):
        if not wsm_only(i,bot) or not staff(i): return await deny(i)
        ch=channel_named(i.guild,'📍・territories')
        if ch is None: return await deny(i,'Existing #territories channel not found.')
        await i.response.defer(ephemeral=True)
        db=bot.store
        rid=db.execute('INSERT INTO missions(guild_id,title,description,points,creator_id,channel_id,created_at) VALUES(?,?,?,?,?,?,?)',
                       (i.guild.id,title,details,points,i.user.id,ch.id,int(time.time()))).lastrowid
        e=embed(f'🗺️ WSM RP MISSION #{rid}: {title}',f'**Reward:** {points} RP points\n**Posted by:** {i.user.mention}\n\n{discord.utils.escape_mentions(details)}\n\nJoin below. Points are only awarded when leadership marks the RP mission complete.')
        try:
            msg=await ch.send(embed=e,view=MissionView(bot,rid),allowed_mentions=discord.AllowedMentions.none())
            db.execute('UPDATE missions SET message_id=? WHERE id=?',(msg.id,rid))
            await private_log(bot,i.guild,i.user.id,'create-mission',note=f'#{rid} {title}')
            await i.followup.send(f'✅ RP mission #{rid} posted in {ch.mention}.',ephemeral=True)
        except discord.Forbidden:
            db.execute('DELETE FROM missions WHERE id=?',(rid,))
            await i.followup.send('Bot cannot post in #territories. Check permissions.',ephemeral=True)

    @bot.tree.command(name='wsm_missions',description='View open fictional WSM RP missions.')
    async def wsm_missions(i:discord.Interaction):
        if not wsm_only(i,bot) or not isinstance(i.user,discord.Member) or rank_index(i.user)<1:
            return await deny(i,'RP missions are available to full WSM members only.')
        await i.response.send_message(embed=missions_embed(bot,i.guild),ephemeral=True)

    @bot.tree.command(name='wsm_mission_complete',description='High Command: close a RP mission and award all signed-up participants.')
    async def wsm_mission_complete(i:discord.Interaction,mission_id:int):
        if not wsm_only(i,bot) or not staff(i): return await deny(i)
        row=bot.store.one('SELECT * FROM missions WHERE id=? AND guild_id=?',(mission_id,i.guild.id))
        if not row or row['status']!='open': return await deny(i,'Mission missing or already closed.')
        await i.response.defer(ephemeral=True)
        # Atomic transaction: mission cannot award points twice on retries.
        rewarded=bot.store.complete_mission_atomic(i.guild.id,mission_id)
        if rewarded is None:
            return await i.followup.send('Mission already completed by another leader.',ephemeral=True)
        for uid in rewarded:
            await check_achievements(bot,i.guild.id,uid)
        ch=i.guild.get_channel(row['channel_id'])
        if ch and row['message_id']:
            try:
                msg=await ch.fetch_message(row['message_id'])
                await msg.edit(view=None,embed=embed(f'✅ WSM MISSION #{mission_id} COMPLETE',f'**{row["title"]}**\n{len(participants)} members earned {row["points"]} RP points.'))
            except discord.HTTPException: pass
        await private_log(bot,i.guild,i.user.id,'complete-mission',note=f'#{mission_id}, {len(rewarded)} recipients')
        await i.followup.send(f'✅ Mission #{mission_id} complete. Awarded **{row["points"]} points** to {len(rewarded)} signed-up members.',ephemeral=True)

    @bot.tree.command(name='wsm_achievements',description='Reveal your own earned WSM achievements. Locked secret badges stay hidden.')
    async def wsm_achievements(i:discord.Interaction,member:discord.Member|None=None):
        if not wsm_only(i,bot): return await deny(i)
        target=member or i.user
        if target.id!=i.user.id and not staff(i): return await deny(i,'Only leadership can check another member’s achievements.')
        rows=bot.store.all('SELECT achievement,earned_at FROM achievements WHERE guild_id=? AND user_id=? ORDER BY earned_at',(i.guild.id,target.id))
        lines=[f'🏅 **{AWARDS[r["achievement"]][0]}** — {AWARDS[r["achievement"]][1]}' for r in rows if r['achievement'] in AWARDS]
        e=embed(f'🔐 WSM BADGES • {discord.utils.escape_markdown(target.display_name)}','\n'.join(lines) if lines else 'No badges yet. Secret ones appear only once earned.')
        await i.response.send_message(embed=e,ephemeral=True)

    @bot.tree.command(name='wsm_award',description='High Command: give a secret WSM achievement to a member.')
    @app_commands.choices(badge=[app_commands.Choice(name='The Shadow',value='shadow'),app_commands.Choice(name='WSM Legend',value='legend')])
    async def wsm_award(i:discord.Interaction,member:discord.Member,badge:app_commands.Choice[str]):
        if not wsm_only(i,bot) or not commander(i): return await deny(i,'Only Boss or Underboss can award secret badges.')
        if badge.value=='legend' and i.user.id!=i.guild.owner_id and member_rank(i.user)!='👑 WSM Boss':
            return await deny(i,'Only the Boss can award WSM Legend.')
        added=bot.store.add_achievement(i.guild.id,member.id,badge.value)
        if added: await private_log(bot,i.guild,i.user.id,'secret-achievement',member.id,badge.value)
        await i.response.send_message(('🏅 Secret achievement awarded.' if added else 'Member already has this achievement.'),ephemeral=True)

    @bot.tree.error
    async def on_app_command_error(i:discord.Interaction,error:app_commands.AppCommandError):
        logging.exception('WSM slash command failed: %s', error)
        await deny(i, 'An error occurred. Check the bot console for details. No server rebuild is needed.')


def main():
    cfg_file=ROOT/'config.json'
    try:
        raw_id=os.environ.get('WSM_GUILD_ID')
        if not raw_id and cfg_file.exists():
            raw_id=json.loads(cfg_file.read_text(encoding='utf-8'))['server_id']
        server_id=int(raw_id)
        if server_id<10**14: raise ValueError('Server ID too short')
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:
        print('ERROR: Set numeric WSM_GUILD_ID in Railway Variables (not an invite link):',e)
        return 1
    token=os.environ.get('WSM_BOT_TOKEN') or (getpass.getpass('Paste your Discord BOT TOKEN (invisible; press Enter): ').strip() if sys.stdin.isatty() else '')
    if not token:
        print('No token supplied. Exiting safely.')
        return 1
    print('Token accepted (never saved to disk). Connecting...')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    bot=WSBot(server_id,ROOT/'data'/'wsm.sqlite3')
    register_commands(bot)
    try:
        bot.run(token,log_handler=None)
    except discord.PrivilegedIntentsRequired:
        print('ERROR: Turn ON Server Members Intent at Discord Developer Portal → Bot → Privileged Gateway Intents.')
        return 2
    except discord.LoginFailure:
        print('ERROR: invalid bot token. Copy the BOT token, not Client Secret. Never paste token into chat.')
        return 2
    except Exception as exc:
        logging.exception('Bot stopped unexpectedly')
        print('Error:',exc)
        return 2
    return 0

if __name__=='__main__':
    sys.exit(main())
