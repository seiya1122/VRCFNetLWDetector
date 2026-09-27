import discord
from discord.ext import tasks
from discord import app_commands
import aiohttp
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta

# ここに自分のDiscord Botトークンを貼り付けてください。概要→Bot→トークンで発行が可能です。
BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'
# 監視したいワールドの「名前」と「ID」の辞書（リスト）を作ります（10個まで増やせます）
# https://vrchat.com/home/world/(worldid)/info
AVAILABLE_WORLDS = {
    "JapanTutorialWorld": "wrld_bf51e60f-f372-48b1-a757-88ba8331d926",
    "FUJIYAMA": "wrld_f5f8b3dc-6f33-4b34-97f3-83add2fb224d"
}
# ----------------

# Discordのコマンドで選べる「選択肢」を自動生成
WORLD_CHOICES = [app_commands.Choice(name=name, value=wid) for name, wid in AVAILABLE_WORLDS.items()]

DATA_FILE = 'channels.json' # このファイルは消さないでください
WORLDS_FILE = 'worlds_data.json' # このファイルは消さないでください

# --- データの保存・読み込み機能 ---
def load_channels():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            # 古いリスト形式のデータが残っていたら、新しい辞書形式にリセットする安全装置
            if "world" not in data or isinstance(data.get("world"), list):
                data["world"] = {} 
            return data
    return {"vrchat": [], "discord": [], "world": {}}

def save_channels(channels):
    with open(DATA_FILE, 'w') as f:
        json.dump(channels, f)

def load_worlds_data():
    if os.path.exists(WORLDS_FILE):
        with open(WORLDS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_worlds_data(data):
    with open(WORLDS_FILE, 'w') as f:
        json.dump(data, f)

# UTC時間を日本時間（JST）の見やすい形式に変換する関数
def convert_to_jst(iso_str):
    if not iso_str:
        return "不明"
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime('%Y年%m月%d日 %H:%M:%S')
    except Exception:
        return iso_str

target_channels = load_channels()
worlds_data = load_worlds_data()

# --- Botの初期設定 ---
class MyClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        check_status.start()
        check_worlds.start()

client = MyClient()
last_statuses = {"vrchat": "none", "discord": "none"}

@client.event
async def on_ready():
    print(f'{client.user} がログインし、監視を開始しました！')

# ==========================================
# スラッシュコマンド設定 (すべて管理者専用)
# ==========================================
@client.tree.command(name="set_vrchat", description="VRChat障害の通知先を設定します")
@app_commands.default_permissions(administrator=True)
async def set_vrchat(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    if channel_id not in target_channels["vrchat"]:
        target_channels["vrchat"].append(channel_id)
        save_channels(target_channels)
        await interaction.response.send_message("✅ ここを **VRChat障害** の通知先に設定しました。", ephemeral=False)
    else:
        await interaction.response.send_message("⚠️ 既に設定されています。", ephemeral=True)

@client.tree.command(name="set_discord", description="Discord障害の通知先を設定します")
@app_commands.default_permissions(administrator=True)
async def set_discord(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    if channel_id not in target_channels["discord"]:
        target_channels["discord"].append(channel_id)
        save_channels(target_channels)
        await interaction.response.send_message("✅ ここを **Discord障害** の通知先に設定しました。", ephemeral=False)
    else:
        await interaction.response.send_message("⚠️ 既に設定されています。", ephemeral=True)

# 【変更】リストから選んで初期設定する
@client.tree.command(name="set_world", description="【初期設定】通知を受け取りたいワールドをリストから選んで設定します")
@app_commands.describe(target_world="通知を受け取りたいワールドを選択してください")
@app_commands.choices(target_world=WORLD_CHOICES)
@app_commands.default_permissions(administrator=True)
async def set_world(interaction: discord.Interaction, target_world: app_commands.Choice[str]):
    ch_id_str = str(interaction.channel_id)
    if ch_id_str not in target_channels["world"]:
        target_channels["world"][ch_id_str] = []
        
    if target_world.value not in target_channels["world"][ch_id_str]:
        target_channels["world"][ch_id_str].append(target_world.value)
        save_channels(target_channels)
        await interaction.response.send_message(f"✅ このチャンネルを **{target_world.name}** の更新通知先に設定しました！", ephemeral=False)
    else:
        await interaction.response.send_message(f"⚠️ **{target_world.name}** は既にこのチャンネルに設定されています。", ephemeral=True)

# 【新規追加】対象ワールドの変更（追加・削除）を行う
@client.tree.command(name="alert_world", description="【変更】このチャンネルのアラート対象ワールドを追加・削除します")
@app_commands.describe(action="どうしますか？", target_world="どのワールドですか？")
@app_commands.choices(
    action=[app_commands.Choice(name="追加する (Add)", value="add"), app_commands.Choice(name="削除する (Remove)", value="remove")],
    target_world=WORLD_CHOICES
)
@app_commands.default_permissions(administrator=True)
async def alert_world(interaction: discord.Interaction, action: app_commands.Choice[str], target_world: app_commands.Choice[str]):
    ch_id_str = str(interaction.channel_id)
    if ch_id_str not in target_channels["world"]:
        target_channels["world"][ch_id_str] = []

    if action.value == "add":
        if target_world.value not in target_channels["world"][ch_id_str]:
            target_channels["world"][ch_id_str].append(target_world.value)
            save_channels(target_channels)
            await interaction.response.send_message(f"✅ アラート対象に **{target_world.name}** を追加しました！", ephemeral=False)
        else:
            await interaction.response.send_message(f"⚠️ **{target_world.name}** は既に追加されています。", ephemeral=True)
            
    elif action.value == "remove":
        if target_world.value in target_channels["world"][ch_id_str]:
            target_channels["world"][ch_id_str].remove(target_world.value)
            save_channels(target_channels)
            await interaction.response.send_message(f"🗑️ アラート対象から **{target_world.name}** を外しました。", ephemeral=False)
        else:
            await interaction.response.send_message(f"⚠️ **{target_world.name}** はそもそも設定されていません。", ephemeral=True)

@client.tree.command(name="remove_alert", description="このチャンネルのすべての通知を解除します")
@app_commands.default_permissions(administrator=True)
async def remove_alert(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    ch_id_str = str(channel_id)
    removed_services = []
    
    if channel_id in target_channels["vrchat"]:
        target_channels["vrchat"].remove(channel_id)
        removed_services.append("VRChat障害")
    if channel_id in target_channels["discord"]:
        target_channels["discord"].remove(channel_id)
        removed_services.append("Discord障害")
    if ch_id_str in target_channels.get("world", {}):
        del target_channels["world"][ch_id_str]
        removed_services.append("すべてのワールド更新")

    if removed_services:
        save_channels(target_channels)
        await interaction.response.send_message(f"✅ このチャンネルから **{' と '.join(removed_services)}** の通知を解除しました。", ephemeral=False)
    else:
        await interaction.response.send_message("⚠️ 通知先が設定されていません。", ephemeral=True)

# 誰でも使える手動確認コマンド（APIを叩かず、メモリから表示します）
@client.tree.command(name="world_status", description="このチャンネルに登録されているワールドの更新状況を確認します")
async def world_status(interaction: discord.Interaction):
    ch_id_str = str(interaction.channel_id)
    watched_worlds = target_channels.get("world", {}).get(ch_id_str, [])
    
    if not watched_worlds:
        await interaction.response.send_message("⚠️ このチャンネルには監視対象のワールドが登録されていません。", ephemeral=True)
        return

    embed = discord.Embed(title="🌐 登録ワールドの最新状況", color=0x00bfff)
    
    # IDから「分かりやすいワールド名」を逆引きするための辞書を作成
    id_to_name = {v: k for k, v in AVAILABLE_WORLDS.items()}

    for world_id in watched_worlds:
        world_name = id_to_name.get(world_id, "不明なワールド")
        # VRChatに通信せず、裏で5分おきにメモしているセーブデータから読み込むだけ
        last_updated = worlds_data.get(world_id) 
        
        if last_updated:
            jst_time = convert_to_jst(last_updated) # 日本時間に変換
            embed.add_field(name=world_name, value=f"最終更新: {jst_time}", inline=False)
        else:
            embed.add_field(name=world_name, value="データ取得待ち（最大5分お待ちください）", inline=False)

    await interaction.response.send_message(embed=embed)

# ==========================================
# 定期監視処理1（1分おき：通信障害）
# ==========================================
@tasks.loop(minutes=1)
async def check_status():
    # タイムアウトを10秒に設定
    timeout_settings = aiohttp.ClientTimeout(total=10)
    
    global last_statuses
    services = [
        {"name": "VRChat", "key": "vrchat", "url": "https://status.vrchat.com/api/v2/status.json"},
        {"name": "Discord", "key": "discord", "url": "https://discordstatus.com/api/v2/status.json"}
    ]
    
    # Sessionにタイムアウトとヘッダーを一括設定
    async with aiohttp.ClientSession(timeout=timeout_settings, headers=custom_headers) as session:
        for svc in services:
            if not target_channels.get(svc["key"]): # get()を使うとエラー防止に少し安全です
                continue
            try:
                async with session.get(svc["url"]) as response:
                    if response.status == 200:
                        data = await response.json()
                        current_status = data['status']['indicator']
                        
                        if current_status != "none" and current_status != last_statuses[svc["key"]]:
                            for ch_id in target_channels[svc["key"]]:
                                channel = client.get_channel(ch_id)
                                if channel:
                                    await channel.send(f"⚠️ **{svc['name']} 通信障害の可能性**\n{data['status']['description']}\n詳細: https://status.{svc['key']}.com/")
                        
                        elif current_status == "none" and last_statuses[svc["key"]] != "none":
                            for ch_id in target_channels[svc["key"]]:
                                channel = client.get_channel(ch_id)
                                if channel:
                                    await channel.send(f"✅ **{svc['name']} 通信障害復旧**\nシステムは正常に稼働しています。")
                        
                        last_statuses[svc["key"]] = current_status
            except Exception as e:
                # エラー時はスキップ（次の1分後のループまで待機＝クールダウン）
                # print(f"[{svc['name']}] Status API Error: {e}") # テスト時のみ有効化するとエラー原因が分かって便利です
                pass

# ==========================================
# 定期監視処理2（5分おき：ワールド更新）
# ==========================================
@tasks.loop(minutes=5)
async def check_worlds():
    if not target_channels.get("world"):
        return

    headers = {"User-Agent": "VRCFNetLWDetector(Bot)/1.0"}
    params = {"apiKey": "JlE5Jldo5Jibnk5O5hTx6XVqsJu4WJ26"}
    
    # タイムアウトの設定（10秒）を作成
    timeout_settings = aiohttp.ClientTimeout(total=10)

    # Sessionにタイムアウトを適用
    async with aiohttp.ClientSession(timeout=timeout_settings) as session:
        for world_name, world_id in AVAILABLE_WORLDS.items():
            url = f"https://api.vrchat.cloud/api/1/worlds/{world_id}"
            try:
                # ここは変更なし
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        current_updated_at = data.get('updated_at')
                        thumbnail_url = data.get('thumbnailImageUrl', '')
                        
                        if world_id not in worlds_data:
                            worlds_data[world_id] = current_updated_at
                            save_worlds_data(worlds_data)
                            continue

                        if current_updated_at != worlds_data[world_id]:
                            worlds_data[world_id] = current_updated_at
                            save_worlds_data(worlds_data)
                            
                            for ch_id_str, watched_worlds in target_channels["world"].items():
                                if world_id in watched_worlds:
                                    channel = client.get_channel(int(ch_id_str))
                                    if channel:
                                        jst_time = convert_to_jst(current_updated_at)
                                        embed = discord.Embed(
                                            title="🎉 ワールド更新通知",
                                            description=f"**{data.get('name')}** がアップデートされました！",
                                            color=0x00ff00,
                                            url=f"https://vrchat.com/home/world/{world_id}"
                                        )
                                        embed.set_footer(text=f"最終更新: {jst_time}")
                                        if thumbnail_url: 
                                            embed.set_thumbnail(url=thumbnail_url)
                                        await channel.send(embed=embed)

            except Exception as e:
                # タイムアウト等でエラーになってもここでキャッチされる
                print(f"ワールド確認エラー: {e}")
            
            # 安全装置はエラー時で必ず5秒待ってから次へ行く
            await asyncio.sleep(5)

client.run(BOT_TOKEN)