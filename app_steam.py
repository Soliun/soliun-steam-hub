import flet as ft
import requests
import json
import os
import threading

CONFIG_FILE = "config.json"
CACHE_FILE = "library_cache.json"


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main(page: ft.Page):
    page.title = "Soliun Steam Hub"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0E1117"
    page.window.width = 1150
    page.window.height = 760
    page.padding = 20

    config = load_json(CONFIG_FILE, {"api_key": "", "steam_id": ""})
    cache = load_json(CACHE_FILE, {"profile": {}, "games": []})

    current_filter = "all"
    search_query = ""

    # Controles de UI globais
    status_text = ft.Text("", size=13, color="#9BA3B0")
    loading_ring = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)

    # Elementos do Perfil e Métricas
    avatar_img = ft.Image(
        src="https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg",
        width=64,
        height=64,
        border_radius=32,
    )
    profile_name = ft.Text("Perfil não sincronizado", size=20, weight=ft.FontWeight.BOLD)
    profile_sub = ft.Text("Configure sua API Key e SteamID para iniciar", size=13, color="#9BA3B0")

    stat_total_games = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color="#38BDF8")
    stat_total_hours = ft.Text("0h", size=22, weight=ft.FontWeight.BOLD, color="#4ADE80")
    stat_unplayed = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color="#F87171")

    games_grid = ft.GridView(
        expand=True,
        runs_count=5,
        max_extent=240,
        child_aspect_ratio=0.82,
        spacing=14,
        run_spacing=14,
    )

    def format_hours(minutes):
        if minutes == 0:
            return "Nunca jogado"
        hours = minutes / 60
        if hours < 1:
            return f"{minutes} min"
        return f"{hours:.1f} h"

    def update_stats_and_profile():
        prof = cache.get("profile", {})
        games = cache.get("games", [])

        if prof:
            avatar_img.src = prof.get("avatarfull", avatar_img.src)
            profile_name.value = prof.get("personaname", "Jogador Steam")
            state = prof.get("personastate", 0)
            states = {0: "Offline", 1: "Online", 2: "Ocupado", 3: "Ausente", 4: "Soneca"}
            current_game = prof.get("gameextrainfo")
            profile_sub.value = (
                f"Jogando agora: {current_game}"
                if current_game
                else f"Status: {states.get(state, 'Offline')}"
            )

        total_g = len(games)
        total_m = sum(g.get("playtime_forever", 0) for g in games)
        unplayed_g = sum(1 for g in games if g.get("playtime_forever", 0) == 0)

        stat_total_games.value = str(total_g)
        stat_total_hours.value = f"{int(total_m // 60):,}h".replace(",", ".")
        stat_unplayed.value = f"{unplayed_g} ({int((unplayed_g / total_g) * 100) if total_g else 0}%)"

    def render_games():
        games = cache.get("games", [])
        filtered = []

        for g in games:
            name = g.get("name", "")
            mins = g.get("playtime_forever", 0)

            if search_query and search_query.lower() not in name.lower():
                continue
            if current_filter == "unplayed" and mins > 0:
                continue
            if current_filter == "played" and mins == 0:
                continue

            filtered.append(g)

        if current_filter == "most_played":
            filtered.sort(key=lambda x: x.get("playtime_forever", 0), reverse=True)
        else:
            filtered.sort(key=lambda x: x.get("name", "").lower())

        cards = []
        for g in filtered:
            appid = g["appid"]
            name = g.get("name", f"App {appid}")
            mins = g.get("playtime_forever", 0)
            header_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

            badge_color = "#EF4444" if mins == 0 else "#22C55E"

            cards.append(
                ft.Container(
                    bgcolor="#161B22",
                    border_radius=10,
                    border=ft.Border.all(1, "#262C36"),
                    padding=10,
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            ft.Image(
                                src=header_url,
                                width=220,
                                height=103,
                                border_radius=6,
                                fit=ft.BoxFit.COVER,
                                error_content=ft.Container(
                                    bgcolor="#1F2937",
                                    height=103,
                                    border_radius=6,
                                    alignment=ft.Alignment.CENTER,
                                    content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, color="#6B7280"),
                                ),
                            ),
                            ft.Text(
                                name,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Container(expand=True),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Container(
                                        bgcolor="#1F2937",
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                        border_radius=6,
                                        content=ft.Text(
                                            format_hours(mins),
                                            size=11,
                                            color=badge_color,
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                    ),
                                    ft.Text(f"ID: {appid}", size=10, color="#6B7280"),
                                ],
                            ),
                        ],
                    ),
                )
            )

        games_grid.controls = cards
        status_text.value = f"Exibindo {len(filtered)} de {len(games)} jogos"
        page.update()

    def sync_steam_data(e=None):
        api_key = config.get("api_key", "").strip()
        steam_id = config.get("steam_id", "").strip()

        if not api_key or not steam_id:
            open_config_dialog()
            return

        def worker():
            loading_ring.visible = True
            status_text.value = "Sincronizando com a API da Steam..."
            page.update()

            try:
                # 1. Dados do Perfil
                prof_url = (
                    f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
                    f"?key={api_key}&steamids={steam_id}"
                )
                r_prof = requests.get(prof_url, timeout=15).json()
                players = r_prof.get("response", {}).get("players", [])
                if players:
                    cache["profile"] = players[0]

                # 2. Biblioteca de Jogos
                lib_url = (
                    f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
                    f"?key={api_key}&steamid={steam_id}&include_appinfo=true&include_played_free_games=true&format=json"
                )
                r_lib = requests.get(lib_url, timeout=15).json()
                games = r_lib.get("response", {}).get("games", [])

                if not games:
                    status_text.value = "Nenhum jogo retornado. Verifique se 'Detalhes dos jogos' está Público na Steam."
                else:
                    cache["games"] = games
                    save_json(CACHE_FILE, cache)
                    update_stats_and_profile()
                    render_games()
                    status_text.value = f"Sincronizado! {len(games)} jogos carregados."

            except Exception as ex:
                status_text.value = f"Erro ao conectar na Steam: {ex}"
            finally:
                loading_ring.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    def open_config_dialog(e=None):
        key_field = ft.TextField(
            label="Steam Web API Key",
            value=config.get("api_key", ""),
            password=True,
            can_reveal_password=True,
        )
        id_field = ft.TextField(
            label="SteamID64 (17 dígitos)",
            value=config.get("steam_id", ""),
            hint_text="Ex: 76561198000000000",
        )

        def save_and_close(ev):
            config["api_key"] = key_field.value.strip()
            config["steam_id"] = id_field.value.strip()
            save_json(CONFIG_FILE, config)
            dlg.open = False
            page.update()
            sync_steam_data()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Configurar Credenciais Steam"),
            content=ft.Column(
                tight=True,
                width=420,
                controls=[
                    ft.Text(
                        "Suas chaves ficam salvas apenas localmente no arquivo config.json.",
                        size=12,
                        color="#9BA3B0",
                    ),
                    key_field,
                    id_field,
                ],
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _: setattr(dlg, "open", False) or page.update()),
                ft.Button("Salvar e Sincronizar", on_click=save_and_close),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def on_search_change(e):
        nonlocal search_query
        search_query = e.control.value
        render_games()

    def on_filter_change(e):
        nonlocal current_filter
        current_filter = e.control.value
        render_games()

    # Cartões de Métricas
    def stat_card(title, value_control):
        return ft.Container(
            bgcolor="#161B22",
            border=ft.Border.all(1, "#262C36"),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=18, vertical=10),
            content=ft.Column(
                spacing=2,
                controls=[
                    ft.Text(title, size=11, color="#9BA3B0"),
                    value_control,
                ],
            ),
        )

    header = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        controls=[
            ft.Row(
                spacing=14,
                controls=[
                    avatar_img,
                    ft.Column(
                        spacing=2,
                        controls=[profile_name, profile_sub],
                    ),
                ],
            ),
            ft.Row(
                spacing=12,
                controls=[
                    stat_card("Total de Jogos", stat_total_games),
                    stat_card("Horas Jogadas", stat_total_hours),
                    stat_card("Nunca Jogados", stat_unplayed),
                ],
            ),
        ],
    )

    toolbar = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        controls=[
            ft.Row(
                spacing=10,
                controls=[
                    ft.TextField(
                        hint_text="Buscar jogo na biblioteca...",
                        prefix_icon=ft.Icons.SEARCH,
                        width=300,
                        height=42,
                        text_size=13,
                        on_change=on_search_change,
                    ),
                    ft.Dropdown(
                        width=200,
                        value="all",
                        text_size=13,
                        options=[
                            ft.dropdown.Option("all", "Todos (A-Z)"),
                            ft.dropdown.Option("most_played", "Mais Jogados"),
                            ft.dropdown.Option("played", "Já Jogados"),
                            ft.dropdown.Option("unplayed", "Nunca Jogados"),
                        ],
                        on_select=on_filter_change,
                    ),
                ],
            ),
            ft.Row(
                spacing=10,
                controls=[
                    loading_ring,
                    status_text,
                    ft.Button(
                        "Sincronizar",
                        icon=ft.Icons.SYNC,
                        on_click=sync_steam_data,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.SETTINGS,
                        tooltip="Configurar API Key e SteamID",
                        on_click=open_config_dialog,
                    ),
                ],
            ),
        ],
    )

    page.add(
        header,
        ft.Divider(height=16, color="#262C36"),
        toolbar,
        ft.Container(height=6),
        games_grid,
    )

    # Inicialização
    if cache.get("games"):
        update_stats_and_profile()
        render_games()
    elif not config.get("api_key") or not config.get("steam_id"):
        open_config_dialog()
    else:
        sync_steam_data()


if __name__ == "__main__":
    ft.run(main)