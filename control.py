#!/usr/bin/env python3.6
# coding=utf-8
"""

    Wargame Server Control Script
    Original Author: DesertEagle
    Modified By: kissinger
    
    Requirements: 
        pip3 install python-geoip-python3 python-geoip-geolite2

    Performance of autobalance is about 10x faster under pypy3 versus cpython (the default)

    Type-checking:
        mypy control.py --disallow-any-generics --no-implicit-optional --disallow-incomplete-defs --disallow-untyped-defs --disallow-untyped-calls --disallow-any-generics --strict --warn-return-any --warn-redundant-casts --warn-unused-ignores --no-warn-no-return

"""

try:
    from geoip import geolite2 # type: ignore
except ImportError:
    print('geoip will be unavailable. Try `pip3 install python-geoip-python3 python-geoip-geolite2`')

import argparse
import base64
import collections
import copy
import itertools
import math
import os
import queue
import re
import select
import sys
import socket
import struct
import time
import timeit
from enum import IntEnum
from random import random
from statistics import mean
import subprocess
from threading import Thread
from typing import (IO, Any, Callable, Dict, Iterable, List, Match, Optional,
                    Pattern, Tuple, cast)

DIR_PATH = os.path.dirname(os.path.realpath(__file__))

#================================================================================#
# environment parameters 
#================================================================================#
DEFAULT_RCON_PORT = '10842'
DEFAULT_RCON_PASSWORD = 'rcon_password'
WARGAME_PORT = 10001
DEFAULT_CHAT_PATH = "chat.txt"
BADWORDS_PATH = "badwords.txt"
SERVER_LOG_PATH = "serverlog.txt"

#================================================================================#
# your specific lobby's parameters 
#================================================================================#
MIN_PLAYER_LEVEL = 5
LOBBY_RULES = f"[EXPERIMENTAL, type 'commands' for more commands] server rules: strictly no teamkilling (even in self-defense); mark starting zones with flare or chat; minimum player level: {MIN_PLAYER_LEVEL}; no support decks (auto-enforced); offensive language may result in kick/ban"
MIN_VOTES_TO_KICK = 3
MAX_BADWORDS_BEFORE_KICK = 3
MIN_VOTES_TO_ROTATE = 3
MIN_VOTES_TO_YEAR = 3
MIN_VOTES_TO_CHANGE_INCOME = 3
DISCONNECTS_IN_LAST_N_MINUTES_TO_BAN = 1
NUM_DISCONNECTS_IN_N_MINUTES_TO_BAN = 3
GENERAL_BLUE_DECK = "@Hs8KGG5CiPWIZrDQSmUgBUimgjmLJlTw6CeCLEkaM6Y0qHI3ypcoaIjS1JFAKCyxII5KPgkMI3IFSGEjzJ+iq0qzKSiXoA=="
GENERAL_RED_DECK = "@Us8JknYKpymQ0KaIKC4i1CeRZKDIvjGshwUAcYm9aWwckJ+IrSdog7IBCBUkvJGSRwoUIOiNgiPRSidknuJQCBMohSXg"
MAP_POOL = [
    "Destruction_2x2_port_Wonsan_Terrestre",
    "Destruction_2x3_Hwaseong",
    "Destruction_2x3_Esashi",
    "Destruction_2x3_Boseong",
    "Destruction_2x3_Tohoku",
    "Destruction_2x3_Anbyon",
#    "Destruction_3x2_Boryeong_Terrestre",
#    "Destruction_3x2_Taean",
#    "Destruction_3x2_Taebuko",
#    "Destruction_3x2_Sangju",
#    "Destruction_3x2_Montagne_3",
#    "Destruction_3x3_Muju",
#    "Destruction_3x3_Pyeongtaek",
#    "Destruction_3x3_Gangjin"
]

#================================================================================#
# general constants
#================================================================================#
YEAR_MAP = { '1985': 0, '1980': 1, 'any': -1 }
INCOME_MAP = { 'none': 0, 'veryhigh': 5, 'high': 4, 'normal': 3, 'low': 2, 'verylow': 1 }

# COUNTRY_FLAGS = { 'US': '#US', 'UK': '#UK', 'FR': '#FR', 'DE': '#RFA', 'CA': '#CAN', 'DK': '#DAN', 'NO': '#NOR', 'SE': '#SWE', 'AU': '#ANZ', 'NZ': '#ANZ', 'KR': '#ROK', 'JP': '#JAP', 'PL': '#POL', 'RU': '#URSS', 'CN': '#CHI', 'CZ': '#CZ' }
COMMANDS_LIST = '(https://bit.ly/37Ndnw5) try chatting (these work in-game too): stats, balance, kick <player-name>, rotate, rules, wherefrom, year <1980, 1985, any>, income <none, verylow, low, normal, high, veryhigh>, team <teamname>'
SUPPORT_DECK_TYPES = [197, 85, 133]
AIRBORNE_DECK_TYPES = [11, 203]
UNSPEC_DECK_TYPES = [207]
ARMORED_DECK_TYPES = [179, 3]
MOTORIZED_DECK_TYPES = [81]
NON_EXISTENT_CLIENT_ID = 0x0c6c0b

class Side(IntEnum):
    Bluefor = 0
    Redfor = 1

class GameState(IntEnum):
    Lobby = 1
    Game = 2
    Debriefing = 3
    Deployment = 4

class Player:
    """
    Player data structure
    Incapsulates player data manipulation
    """

    def __init__(self, playerid: str, ip: str, port: int) -> None:
        self._id: str = playerid
        self._side: Side = Side.Bluefor
        self._ip: str = ip
        self._port: int = port
        self._deck: str = ""
        self._level: int = 0
        self._elo: float = 0.0
        self._name: str = ""
        self.arrival_time: float = time.time()
        self.num_badwords = 0
        self.team_affiliation: Optional[str] = None
        self.disconnects: List[float] = [] # timestamps of any disconnects
        self.votes: Dict[str, Dict[Any, bool]] = { 'kick': {}, 'rotate': {}, 'year': {}, 'income': {} }

    # Getters
    def get_id(self) -> str:
        return self._id
    
    def get_ip(self) -> str:
        return self._ip

    def get_port(self) -> int:
        return self._port
    
    def get_side(self) -> Side:
        return self._side

    def get_deck(self) -> str:
        return self._deck

    def get_level(self) -> int:
        return self._level

    def get_elo(self) -> float:
        return self._elo

    def get_name(self) -> str:
        return self._name.replace('"', '')

    # Setters
    def set_side(self, side: Side) -> None:
        self._side = side

    def set_deck(self, deck: str) -> None:
        self._deck = deck

    def set_level(self, level: int) -> None:
        self._level = level

    def set_elo(self, elo: float) -> None:
        self._elo = elo

    def set_name(self, name: str) -> None:
        self._name = name

    # ------------------------------
    # Manipulation logic for the player
    # ------------------------------

    def swap_side(self) -> None:
        """Forcibly change player's side to opposite of what it is now"""
        if self.get_side() == Side.Bluefor:
            side = Side.Redfor
        else:
            side = Side.Bluefor
        self.change_side(side)
        
    def set_default_deck(self) -> None:
        if self.get_side() == Side.Bluefor:
            self.change_deck(GENERAL_BLUE_DECK)
        else:
            self.change_deck(GENERAL_RED_DECK)
    
    def change_side(self, side: int) -> None:
        """Forcibly change player's side"""
        Rcon.execute("setpvar " + self._id + " PlayerAlliance " + str(int(side)))
        #if side == Side.Bluefor:
        #    self.change_deck(GENERAL_BLUE_DECK)
        #else:
        #    self.change_deck(GENERAL_RED_DECK)
            
    def change_deck(self, deck: str) -> None:
        """Forcibly assign new deck to a player"""
        Rcon.execute("setpvar " + self._id + " PlayerDeckContent " + deck)

    def kick(self) -> None:
        """Kick player"""
        Server.kick_player_by_id(self._id)

    def ban(self) -> None:
        """Ban player"""
        Server.ban_player_by_id(self._id)
    


class Deck:

    @classmethod
    def get_deck_type(cls, deck_str: str) -> int:
        bytes = base64.b64decode(deck_str)
        return bytes[1]
    
    @classmethod
    def is_support_deck(cls, deck_str: str) -> bool:
        try:
            type_code = Deck.get_deck_type(deck_str)
            #print(bytes[0]) # country
            return type_code in SUPPORT_DECK_TYPES
        except Exception:
            print(f'invalid deck code: {deck_str}')
            return False # default to False, if it's invalid...it can't be support?


class Server:
    """
    Server data structure
    Incapsulates server manipulation
    """
    @classmethod
    def send_message(cls, message: str, from_client_id: int, only_to_client_id:Optional[str]=None) -> None:
        print(f'[SERVER]: {message}')
        """Send a message. If not client specified, will go to all clients"""
        if not only_to_client_id:
            client_id_hex = 0xffffffff # broadcast
        else:
            client_id_hex = int(only_to_client_id, 16)
            assert client_id_hex < 0xffffffff

        # TODO: this is not working yet in the patched binary, so we're just always setting it to the client id. remove this line once it works.
        client_id_hex = 0xffffffff

        source_client_id_hex = from_client_id

        # strip the 0x prefix on the hex client id
        msg = f"chat {'%08x' % client_id_hex} {'%08x' % source_client_id_hex} {message}"
        Rcon.execute(msg)
        
    @classmethod
    def change_map(cls, mapname: str) -> None:
        Rcon.execute("setsvar Map " + mapname)

    @classmethod
    def change_game_type(cls, game_type: int) -> None:
        Rcon.execute("setsvar GameType " + str(game_type))

    @classmethod
    def change_name(cls, name: str) -> None:
        Rcon.execute("setsvar ServerName " + name)

    @classmethod
    def ban_player_by_id(cls, id: str) -> None:
        Rcon.execute("ban " + id)
        if os.path.exists('banned_clients.ini'):
            with open('banned_clients.ini', 'a') as fout:
                fout.write(f"{id} = 0\n") # ban forever

    @classmethod
    def kick_player_by_id(cls, id: str) -> None:
        Rcon.execute("kick " + id)

    @classmethod
    def change_income_rate(cls, number: int) -> None:
        if 0 <= number <= 5:
            Rcon.execute("setsvar IncomeRate " + str(number))
        else:
            print('valid number for income: 0-5')

    @classmethod
    def change_min_players_to_start(cls, number: int) -> None:
        Rcon.execute("setsvar NbMinPlayer " + str(number))
        
    @classmethod
    def change_time_limit(cls, number: int) -> None:
        Rcon.execute("setsvar TimeLimit " + str(number))

    @classmethod
    def change_max_players(cls, number: int) -> None:
        Rcon.execute("setsvar NbMaxPlayer " + str(number))

    @classmethod
    def change_money(cls, number: int) -> None:
        Rcon.execute("setsvar InitMoney " + str(number))

    @classmethod
    def change_score_limit(cls, number: int) -> None:
        Rcon.execute("setsvar ScoreLimit " + str(number))

    @classmethod
    def change_victory_cond(cls, number: int) -> None:
        Rcon.execute("setsvar VictoryCond " + str(number))

    @classmethod
    def change_date_constraint(cls, number: int) -> None:
        Rcon.execute("setsvar DateConstraint " + str(number))
    
class Rcon:
    """ Rcon connection settings """
    rcon_host: str = "localhost"
    rcon_port: str = DEFAULT_RCON_PORT
    rcon_password: str = DEFAULT_RCON_PASSWORD

    @classmethod
    def execute(cls, command: str) -> None:
        """Execute rcon command, incapsulating details"""
        client = PyRcon()
        client.connect(
            cls.rcon_host,
            cls.rcon_port,
            cls.rcon_password,
        )
        return client.command(command)

class Game:
    """Main class, containing game process manipulation"""
    lines_processed = 0 # number of lines read from the serverlog.txt

    # -------------------------------------------
    # User event handlers
    # -------------------------------------------
    
    def on_player_connect(self, playerid: str) -> None:
        known_player = self.known_players.get(playerid)
        if known_player:
            current_time = time.time()
            print(f'for {known_player.get_name()} disconnects are {[int(current_time - t) for t in known_player.disconnects]}')
            known_player.disconnects = [t for t in known_player.disconnects if current_time - t <= DISCONNECTS_IN_LAST_N_MINUTES_TO_BAN * 60]
            if len(known_player.disconnects) >= NUM_DISCONNECTS_IN_N_MINUTES_TO_BAN:
                self.send_message(f'player {known_player.get_name()} banned for excessive leave/join behavior', lobby_only=True)
                known_player.ban()

        else: # new player, send them the rules
            pass #Server.send_message(LOBBY_RULES, playerid)

        # if we now have n-1 or n-2 clients, let's autobalance
        if self.minPlayersToStart > 0 and len(self.players) >= self.minPlayersToStart - 2:
            self.balance(execute=False) # TODO

    def on_player_deck_set(self, playerid: str, playerdeck: str) -> None:
        if Deck.is_support_deck(playerdeck):
            p = self.players.get(playerid)
            if p:
                p.set_default_deck()
                self.send_message(f'{p.get_name()}: support deck disallowed by server rules. Resetting deck', lobby_only=True)

    def on_player_message(self, client_id: str, msg: str) -> None:
        # find the player id
        from_player = self.players.get(client_id)
        if not from_player:
            print('error: player not found for id: ' + client_id)
            return

        print('[' + str(from_player.get_id()) + ':' + from_player.get_name() + ']: ' + msg)

        for badword in self.badwords.keys():
            if badword in msg.lower():
                from_player.num_badwords += 1
                if from_player.num_badwords > MAX_BADWORDS_BEFORE_KICK:
                    self.send_message(f'player {from_player.get_name()} kicked for language')
                    from_player.kick()
                else:
                    print(f'player {from_player.get_name()} used badword: {badword}')
                break
        
        if msg == 'rules':
            print('sending rules')
            self.send_message(LOBBY_RULES)
        elif msg.startswith('kick '):
            self.handle_kick_request(msg, from_player)
        elif msg.startswith('team '):
            self.handle_team_affiliation(msg, from_player)
        elif msg == 'stats':
            self.message_average_team_info(True)
        elif msg in ['commands', 'command', 'cmd', 'comand', 'comands', "'commands'", '"commands"']:
            self.send_message(COMMANDS_LIST)
        elif msg == 'rotate':
            self.handle_rotate_request(from_player)
        elif msg == 'balance':
            self.handle_balance_request(from_player)
        elif msg.startswith('year '):
            self.handle_year_request(msg, from_player)
        elif msg.startswith('income '):
            self.handle_income_request(msg, from_player)
        elif msg == 'wherefrom':
            s = []
            for player in self.players.values():
                match = geolite2.lookup(player.get_ip())
                if match:
                    suffix = match.country #COUNTRY_FLAGS.get(match.country, match.country)
                    s.append(f'{player.get_name()}: {suffix}')
            self.send_message(', '.join(s), lobby_only=False)


    def on_player_level_set(self, playerid: str, playerlevel: int) -> None:
        self.limit_level(playerid, playerlevel)

    def on_player_elo_set(self, playerid: str, playerelo: float) -> None:
        pass

    def on_player_side_change(self, playerid: str, playerside: Side) -> None:
        pass

    def on_player_name_change(self, playerid: str, playername: str) -> None:
        pass

    def on_player_disconnect(self, playerid: str) -> None:
        pass

    def on_switch_to_game(self) -> None:
        self.send_message(LOBBY_RULES)

    def on_switch_to_debriefing(self) -> None:
        self.map_random_rotate()

    def on_switch_to_deployment(self) -> None:
        self.send_message(LOBBY_RULES)

    def on_switch_to_lobby(self) -> None:
        pass

    # -------------------------------------------
    # Custom actions
    # -------------------------------------------

    def count_votes(self, vote_category: str, vote_value: Any, same_team: bool) -> int:
        acc = 0
        for player in self.players.values():
            if vote_value in player.votes[vote_category]:
                if same_team:
                    # means vote_value must be a player id. make sure that the
                    # player is on the same team as the person being voted about
                    target_player = self.players.get(vote_value)
                    if target_player and target_player.get_side() == player.get_side():
                        acc += 1
                else:
                    acc +=1
        return acc

    def send_message(self, message: str, lobby_only: bool=False) -> None:
        # who is the message from?
        if lobby_only or self.gameState == GameState.Lobby:
            Server.send_message(message, NON_EXISTENT_CLIENT_ID)
        else:
            possible_hosts = list(self.players.keys())
            if len(possible_hosts):
                host = sorted(possible_hosts)[0]
                print(f'selected host {self.players[host].get_id()} for message {message}')
                Server.send_message(message, int(self.players[host].get_id()))
                # at least it will show up in the lobby so that I can see it
                Server.send_message(message, NON_EXISTENT_CLIENT_ID)

    def find_player_id_by_name(self, name: str, strict: bool=True) -> Optional[Player]:
        for player in self.players.values():
            if player.get_name() == name:
                return player
        if not strict:
            for player in self.players.values():
                if player.get_name().lower().startswith(name):
                    return player        
        return None

    def find_player_id_by_ip(self, ip: str, port: int) -> Optional[Player]:
        for _playerID, player in self.players.items():
            if player.get_ip() == ip and player.get_port() == port:
                return player
        return None

    def handle_balance_request(self, from_player: Player) -> None:
        self.balance()

    def handle_team_affiliation(self, msg: str, from_player: Player) -> None:
        team_requested = msg.split(' ')[1]
        from_player.team_affiliation = team_requested
        self.send_message(f'in autobalance, {from_player.get_name()} will stay on the same side as all players on: {team_requested}', lobby_only=True)
    
    def handle_rotate_request(self, from_player: Player) -> None:
        from_player.votes['rotate'][1] = True
        nvotes = self.count_votes('rotate', 1, same_team=False)
        nvotes_needed = min(MIN_VOTES_TO_ROTATE, len(self.players))
        if nvotes >= nvotes_needed:
            self.map_random_rotate()
            for player in self.players.values():
                player.votes['rotate'] = {}
        else:
            self.send_message(str(nvotes) + '/' + str(nvotes_needed) + ' votes to rotate', lobby_only=True)

    def handle_year_request(self, msg: str, from_player: Player) -> None:
        year = msg.split(' ')[1]
        if year not in YEAR_MAP:
            self.send_message("Unknown year, options are: " + ', '.join(YEAR_MAP.keys()), lobby_only=True)
            return
        from_player.votes['year'][year] = True
        nvotes = self.count_votes('year', year, same_team=False)
        nvotes_needed = min(MIN_VOTES_TO_YEAR, len(self.players))
        self.send_message(str(nvotes) + '/' + str(nvotes_needed) + ' votes to set year to: ' + year, lobby_only=True)
        if nvotes >= nvotes_needed:
            Server.change_date_constraint(YEAR_MAP[year])
            # after that, we need to force all the decks -- this kicks people with the wrong year though!
            # self.assign_decks()
            for player in self.players.values():
                player.votes['year'] = {}

    def handle_income_request(self, msg: str, from_player: Player) -> None:
        newincome = msg.split(' ')[1].lower()
        if newincome not in INCOME_MAP:
            self.send_message("Unknown income, options are: " + ', '.join(INCOME_MAP.keys()), lobby_only=True)
            return
        from_player.votes['income'][newincome] = True
        nvotes = self.count_votes('income', newincome, same_team=False)
        nvotes_needed = min(MIN_VOTES_TO_CHANGE_INCOME, len(self.players))
        self.send_message(str(nvotes) + '/' + str(nvotes_needed) + ' votes to set income to: ' + newincome, lobby_only=True)
        if nvotes >= nvotes_needed:
            Server.change_income_rate(INCOME_MAP[newincome])
            for player in self.players.values():
                player.votes['income'] = {}

                
    def handle_kick_request(self, msg: str, from_player: Player) -> None:
        parts = msg[len('kick '):]
        kickable_player = self.find_player_id_by_name(parts, strict=False)
        if kickable_player:
            from_player.votes['kick'][kickable_player.get_id()] = True
            nvotes = self.count_votes('kick', kickable_player.get_id(), same_team=True)
            if kickable_player.get_side() == from_player.get_side():
                self.send_message(str(nvotes) + '/' + str(MIN_VOTES_TO_KICK) + ' votes from same team to kick ' + kickable_player.get_name())
            else:
                self.send_message('kick vote rejected: not on same team')
            if nvotes >= MIN_VOTES_TO_KICK:
                kickable_player.kick()
                for player in self.players.values():
                    if kickable_player.get_id() in player.votes['kick']:
                        del player.votes['kick'][kickable_player.get_id()]
        else:
            self.send_message(f"player '{parts}' not found")
    
    def assign_decks(self) -> None:
        """Forcing specific deck usage"""
        for player in self.players.values():
            if player.get_side() == Side.Bluefor:
                if player.get_deck() != GENERAL_BLUE_DECK:
                    player.change_deck(GENERAL_BLUE_DECK)

            if player.get_side() == Side.Redfor:
                if player.get_deck() != GENERAL_RED_DECK:
                    player.change_deck(GENERAL_RED_DECK)

    def map_random_rotate(self) -> None:
        """Rotate maps from the pool, making sure not to select the same one again!"""
        new_id = self.currentMapId
        while self.currentMapId == new_id:
            new_id = math.floor(len(MAP_POOL) * random())
        Server.change_map(MAP_POOL[new_id])
        print(f"Rotating map to {MAP_POOL[new_id]}")

    def limit_level(self, playerid: str, playerlevel: int) -> None:
        """Kick players below certain level"""
        limit = MIN_PLAYER_LEVEL
        if playerlevel < limit:
            msg = (f'{self.players[playerid].get_name()} level ({playerlevel}) is too low, minimum is {limit}. Sorry! Kicking...')
            self.send_message(msg, lobby_only=True)
            self.players[playerid].kick()

# ----------------------------------------------------------------------------------------------------------------------
# --------------------------------------- INTERNAL IMPLEMENTATION DETAILS ----------------------------------------------
# ----------------------------------------------------------------------------------------------------------------------

    # -------------------------------------------
    # Service event handlers
    # -------------------------------------------

    def _on_player_connect(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        player_ip = match_obj.group(2)
        player_port = match_obj.group(3) 
        # Creating player data structure if not present
        if not (playerid in self.players):
            print(f"connected player {playerid}")
            self.players[playerid] = Player(playerid, player_ip, int(player_port))
        

        if not self.infoRun:
            self.on_player_connect(playerid)

    # ----------------------------------------------
    def _on_player_deck_set(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        playerdeck = match_obj.group(2)

        if playerid not in self.players:
            print(f'warn: player id {playerid} not found')
            return None

        self.players[playerid].set_deck(playerdeck)

        if not self.infoRun:
            self.on_player_deck_set(playerid, playerdeck)

    # ----------------------------------------------
    def _on_player_level_set(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        playerlevel = match_obj.group(2)

        if playerid not in self.players:
            print(f'warn: player id {playerid} not found')
            return None


        self.players[playerid].set_level(int(playerlevel))

        if not self.infoRun:
            self.on_player_level_set(playerid, int(playerlevel))

    # ----------------------------------------------
    def _on_player_elo_set(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        playerelo = float(match_obj.group(2))

        self.players[playerid].set_elo(playerelo)

        if not self.infoRun:
            self.on_player_elo_set(playerid, playerelo)

    # ----------------------------------------------
    def _on_player_disconnect(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)

        if playerid in self.players:            
            self.known_players[playerid] = self.players[playerid]
            self.known_players[playerid].disconnects.append(time.time())
            print(f"removing player {playerid}")
            del self.players[playerid]

            if not self.infoRun:
                self.on_player_disconnect(playerid)
        else:
            print(f'WARNING: {playerid} not found')


    # ----------------------------------------------
    def _on_player_side_change(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        side = Side.Redfor if match_obj.group(2) == '1' else Side.Bluefor

        if playerid in self.players:
            self.players[playerid].set_side(side)

            if not self.infoRun:
                self.on_player_side_change(playerid, side)
        else:
            print(f'WARNING: {playerid} not found')
                

    # ----------------------------------------------
    def _on_player_name_change(self, match_obj: Match[str]) -> None:

        playerid = match_obj.group(1)
        playername = match_obj.group(2)
        self.players[playerid].set_name(playername)

        if not self.infoRun:
            self.on_player_name_change(playerid, playername)

    # ----------------------------------------------
    def _on_switch_to_game(self, match_obj: Match[str]) -> None:
        self.gameState = GameState.Game

        if not self.infoRun:
            self.on_switch_to_game()

    # ----------------------------------------------
    def _on_switch_to_debriefing(self, match_obj: Match[str]) -> None:
        self.gameState = GameState.Debriefing

        if not self.infoRun:
            self.on_switch_to_debriefing()

    # ----------------------------------------------
    def _on_switch_to_lobby(self, match_obj: Match[str]) -> None:
        self.gameState = GameState.Lobby

        if not self.infoRun:
            self.on_switch_to_lobby()

    # ----------------------------------------------
    def _on_switch_to_deployment(self, match_obj: Match[str]) -> None:
        self.gameState = GameState.Deployment

        if not self.infoRun:
            self.on_switch_to_deployment()

    # ----------------------------------------------
    def _on_set_min_players(self, match_obj: Match[str]) -> None:
        min_players = match_obj.group(1)
        self.minPlayersToStart = int(min_players)
        print(f'min players is {self.minPlayersToStart}')

        if not self.infoRun:
            pass
            
    # ---------------------------------------------
    # Event handlers registration
    # ---------------------------------------------

    def register_events(self) -> None:
        self.register_event(r'Client added in session \(EugNetId : ([0-9]+).+IP : ([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}):([0-9]+)', self._on_player_connect)
        self.register_event('Client ([0-9]+) variable PlayerDeckContent set to "(.*)"', self._on_player_deck_set)
        self.register_event('Client ([0-9]+) variable PlayerLevel set to "(.*)"', self._on_player_level_set)
        self.register_event('Client ([0-9]+) variable PlayerElo set to "(.*)"', self._on_player_elo_set)
        self.register_event('Client ([0-9]+) variable PlayerAlliance set to "([0-9])"', self._on_player_side_change)
        self.register_event('Client ([0-9]+) variable PlayerName set to "(.*)"', self._on_player_name_change)
        self.register_event('Disconnecting client ([0-9]+)', self._on_player_disconnect)
        self.register_event('Entering in loading phase state', self._on_switch_to_game)
        self.register_event('Entering in deploiement phase state', self._on_switch_to_deployment)
        self.register_event('Entering in debriephing phase state', self._on_switch_to_debriefing)
        self.register_event('Entering in matchmaking state', self._on_switch_to_lobby)
        self.register_event('Variable NbMinPlayer set to "(.*)"', self._on_set_min_players)

    # -------------------------------------------
    # Utility functions
    # -------------------------------------------

    def __init__(self) -> None:
        self.last_message: Optional[str] = None
        self.badwords: Dict[str, bool] = {}
        self.events: Dict[Pattern[str], Callable[[Match[str]], None]] = {}
        self.players: Dict[str, Player] = {}
        self.known_players: Dict[str, Player] = {}
        self.gameState: GameState = GameState.Lobby
        self.minPlayersToStart: int = 0
        self.infoRun: bool = True
        self.register_events()
        self.currentMapId = -1
        self.tick_count = 0

    def load_badwords_if_present(self) -> None:
        if os.path.exists(BADWORDS_PATH):
            with open(BADWORDS_PATH) as badf:
                for line in badf.readlines():
                    line = line.replace('*', '').strip().lower()
                    if len(line):
                        self.badwords[line] = True
                print(f'loaded {len(self.badwords)} badwords from: {BADWORDS_PATH}')
        else:
            print(f'no badwords found at: {BADWORDS_PATH}')

    def main(self) -> None:
        print("Server control script started")
        print("Gather information run")

        self.load_badwords_if_present()

        while self.infoRun:
            # spin until serverlog is processed
            pass 
        print(f"Gather information run is complete: {self.lines_processed} lines processed")

        print('Server control started, type "help" for help')
        first_run = True
        while True:
            self.run_cli(first_run)
            first_run = False
            #self.dump_state()
            self.message_average_team_info()
            if self.tick_count % 60 == 0:
                self.send_message("chat 'commands' for a list of commands")
            self.tick_count += 1


    def run_cli(self, first_run: bool) -> None:
        if first_run:
            print('>> ', end='', flush=True)
        help_msg = '''
Server.change_income_rate(2)
Server.change_map('map_name')
Server.change_name('name')
Server.change_min_players_to_start(20)
Server.change_time_limit(1500)
Server.change_max_players(10)
Server.change_money(1000)
Server.change_score_limit(5000)
Server.change_victory_cond(1)
Server.kick_player_by_id('000000')
Server.ban_player_by_id('000000')
Server.send_message('CCCCCCCCCCCCCCCCCCCCCCCC', NON_EXISTENT_CLIENT_ID) (0x43 stream)
dump
game.map_random_rotate()
'''
        CLI_TIMEOUT = 100 # seconds
        i, o, e = select.select( [sys.stdin], [], [], CLI_TIMEOUT)
        if (i):
            user_input = sys.stdin.readline().strip()
            if user_input == 'help':
                print(help_msg)
            elif user_input == 'dump':
                self.dump_state()
            elif user_input.startswith('swap '):
                target = user_input.split(' ')[1]
                self.players[target].swap_side()
            elif user_input.startswith('deck'):
                target = user_input.split(' ')[1]
                deck = user_input.split(' ')[2]
                self.players[target].change_deck(deck)
            else:
                print("COMMAND: ", user_input)
                try:
                    exec(user_input)
                except Exception as e:
                    print('command failed: ' + str(e))

    def average_player_level(self, players: Iterable[Player], side: Side) -> float:
        lvls = [player.get_level() for player in players if player.get_side() == side]
        if len(lvls) == 0:
            return 0
        return mean(lvls)

    def get_avg_team_msg(self) -> str:
        blue = 'average blue: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Bluefor))
        red = 'average red: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Redfor))
        msg = blue + " - " + red
        return msg
    
    def message_average_team_info(self, force: bool=False) -> None:
        msg = self.get_avg_team_msg()
        if msg != self.last_message or force:
            self.send_message(msg, lobby_only=True)
        self.last_message = msg

    def balance(self, execute: bool=False, quiet: bool=False) -> None:
        players = copy.deepcopy(self.players)
        num_players = len(players)
        by_level: Tuple[Tuple[int, str, Optional[str], int], ...] = tuple((player.get_level(), player.get_id(), player.team_affiliation, int(player.get_side())) for player in players.values())
        suggestion_raw = balance_internal(by_level)
        if suggestion_raw is None:
            print('could not generate balance suggestion!')
        else:
            playerids = [x[1] for x in by_level]
            suggestion = [(playerid, Side.Bluefor) if side == 0 else (playerid, Side.Redfor) for (side, playerid) in zip(suggestion_raw, playerids)]
            suggest_text = 'swap '
            had_suggestion = False
            for playerid, side in suggestion:
                if players[playerid].get_side() != side:
                    had_suggestion = True
                    suggest_text += f"'{players[playerid].get_name()}' to {('blue' if side == Side.Bluefor else 'red')}, "
                if execute:
                    players[playerid].change_side(side)

            blues = [players[playerid].get_level() for playerid, side in suggestion if side == Side.Bluefor]
            reds = [players[playerid].get_level() for playerid, side in suggestion if side == Side.Redfor]
            if execute:
                self.send_message("teams have been autobalanced. if you want to stay on the same side as a friend, both chat 'team XYZ'", lobby_only=True)
                self.message_average_team_info()
            else:
                if had_suggestion:
                    if len(blues) and len(reds):
                        if not quiet: self.send_message(f'suggestion: {suggest_text}new stats: blue avg: {int(mean(blues))}, red avg: {int(mean(reds))}', lobby_only=True)
                else:
                    if not quiet: self.send_message(f"{2**(num_players)} possibilities tried, can't do any better than what we have right now: {self.get_avg_team_msg()}", lobby_only=True)
        
    def dump_state(self) -> None:
        #print(chr(27) + "[2J")
        
        print("We have {} players:".format(len(self.players)))
        for player in sorted(self.players.values(), key=lambda x: str(x.get_side())):
            print('[{}] {}:\t{}\t{}\t\tdeck-type:{}'.format(str(player.get_side()), str(player.get_level()), player.get_id(), player.get_name(), Deck.get_deck_type(player.get_deck())))
        print('-------------')
        print('avg blue: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Bluefor)))
        print('avg red: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Redfor)))
        print('-------------')

    def register_event(self, regex: str, handler: Callable[[Match[str]], None]) -> None:
        """Register event handler for a certain log entry"""
        self.events[re.compile(regex)] = handler

    def update(self) -> int:
        """Parse log and trigger event handler"""
        counter = 0
        for line_i, line in enumerate(open(SERVER_LOG_PATH).readlines()):
            counter += 1
            if counter < self.lines_processed + 1:
                continue
            # Test against event expressions
            #print(f"DEBUG {line}", {self.lines_processed}, {counter})
            for pair in self.events.items():
                match = pair[0].match(line)
                if match:
                    pair[1](match)
        return counter



def balance_internal(by_level: Tuple[Tuple[int, str, Optional[str], int], ...]) -> Optional[Tuple[int, ...]]:
    # what is the minimal edit distance to minimize the level differences, honoring team affiliation requests?
    # let's just try them all :) only 2**20 max
    best: Optional[float] = None
    best_set: Optional[Tuple[int]] = None

    def scoring_function(sum_red: float, sum_blue: float, switches: int) -> float:
        """
        If the number of switches is zero, don't multiply. If the difference is
        absolutely the same, set that to one so that multipying by switches is still
        meaningful.
        """
        return (abs((sum_red / 1) - (sum_blue / 1)) + 1) * (1 if switches == 0 else switches)

    def score_balance(sides: Tuple[int, ...],
                      by_level: Tuple[Tuple[int, str, Optional[str], int], ...],
                      max_team_size: int,
                      current_sides: Tuple[int, ...],
                      current_best: Optional[float]) -> Optional[float]:
        """
        Return the averages of the two sides, taking into account whether all players got to be on their preferred team.
        """
        num_blue, num_red = 0, 0
        sum_blue, sum_red = 0, 0
        for side in sides:
            if side == 0:
                num_blue += 1
            else:
                num_red += 1

        # can't have more than max_team_size on one side or the other
        if num_blue > max_team_size or num_red > max_team_size:
            return None

        for side, x in zip(sides, by_level):
            if side == 0:
                sum_blue += x[0]
            else:
                sum_red += x[0]
        
        # shortcut: if, even with 0 swithches, this wouldn't be better than what we have, let's skip it!
        if current_best and scoring_function(sum_blue, sum_red, 0) > current_best:
            return None

        blue_teams, red_teams = set([]), set([])
        for side, x in zip(sides, by_level):
            if x[2] != None:
                if side == 0:
                    blue_teams.add(x[2])
                else:
                    red_teams.add(x[2])

        # make sure everyone who wanted to be on the same team is on the same team!
        if blue_teams.isdisjoint(red_teams):
            # number of switches:
            switches = sum(1 if current_side != proposed_side else 0 for current_side, proposed_side in zip(current_sides, sides))
            return scoring_function(sum_red, sum_blue, switches)
        else:
            return None

    max_team_size = math.ceil(len(by_level) / 2)
    current_sides: Tuple[int, ...] = tuple([side for (_, _, _, side) in by_level])
    original_score = score_balance(current_sides, by_level, max_team_size, current_sides, None)
    for combination in itertools.product([0, 1], repeat=len(by_level)):
        combination = cast(Tuple[int], combination)
        # if sum(combinations) != num_players / 2 -- only if we are assuming even number of players
        score = score_balance(combination, by_level, max_team_size, current_sides, best)
        #print(f'combo: {combination} score: {score}')
        if score is not None:
            if best is None or score < best:
                best = score
                best_set = combination
    print(f'best set: {best_set} with score: {best} (original: {original_score})')
    return best_set

def test_balance() -> None: 
    assert (balance_internal(((5, 'bad', None, 0), (6, 'good', None, 0)))) == (0, 1)
    # in this case 001 and 110 both have the same score. The algorithm should prefer not switching people who are already on the same team
    assert (balance_internal(((15, 'great', None, 0), (6, 'good', None, 0), (8, 'middle', None, 0)))) == (1, 0, 0)
    assert (balance_internal(((15, 'great', None, 0), (16, 'best', None, 0), (6, 'good', None, 0), (8, 'middle', None, 0)))) == (0, 1, 1, 0)
    assert (balance_internal(((15, 'great', None, 1), (6, 'good', None, 1), (8, 'middle', None, 1)))) == (0, 1, 1)
    assert (balance_internal(((15, 'great', None, 1), (16, 'best', None, 0), (6, 'good', None, 1), (8, 'middle', None, 1)))) == (1, 0, 0, 1)
    assert (balance_internal(((15, 'great', None, 1), (16, 'best', None, 0), (6, 'good', None, 0), (8, 'middle', None, 1)))) == (1, 0, 0, 1)
    assert (balance_internal(((15, 'great', None, 0), (16, 'best', None, 0), (6, 'good', None, 1), (8, 'middle', None, 1)))) == (0, 1, 1, 0)        

    print('test team affiliation...')
    assert (balance_internal(((15, 'great', 'team1', 0), (6, 'good', 'team2', 1), (8, 'middle', 'team1', 1)))) == (0, 1, 0)

    print('test team affiliation over max size...')
    assert balance_internal((
        (15, 'great', 'team1', 0), (6, 'good', 'team1', 1), (8, 'middle', 'team1', 1),
        (15, 'great', 'team1', 0), (6, 'good', 'team1', 1), (8, 'middle', 'team1', 1),
        (15, 'great', 'team1', 0), (6, 'good', 'team1', 1), (8, 'middle', 'team1', 1),
        (15, 'great', 'team1', 0), (6, 'good', 'team1', 1), (8, 'middle', 'team1', 1)
    )) == None # everyone wants to be on the same team--can't do it!

    
    assert (balance_internal((
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (15, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', None, 0),
        (30, 'great', 'team1', 1),
        (30, 'great', 'team1', 1)
    ))) == (0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1)

    assert (balance_internal((
        (38, 'p1', None, 1),
        (20, 'p2', None, 1),
        (16, 'p3', None, 1),
        (37, 'p4', None, 1),
        (14, 'p5', None, 1),
        (36, 'p6', None, 1),
        (37, 'p7', None, 1),
        (7, 'p8', None, 1),
        (47, 'p9', None, 1),
        (15, 'p10', None, 1),
        (19, 'p11', None, 0),
        (26, 'p12', None, 0),
        (29, 'p13', None, 0),
        (12, 'p14', None, 0),
        (17, 'p15', None, 0),
        (5, 'p16', None, 0),
        (29, 'p17', None, 0),
        (41, 'p18', None, 0)
    ))) == (0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0)


class PyRconException(Exception):
    pass


class PyRcon(object):
    socket = None

    def connect(self, host: str, port: str, password: str):
        if self.socket is not None:
            raise PyRconException("Already connected")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((host, int(port)))
        self.send(3, password)

    def disconnect(self):
        if self.socket is None:
            raise PyRconException("Already disconnected")
        self.socket.close()
        self.socket = None

    def read(self, length: int):
        data = b""
        while len(data) < length:
            data += self.socket.recv(length - len(data))
        return data

    def send(self, out_type: int, out_data: str):
        if self.socket is None:
            raise PyRconException("Must connect before sending data")

        # Send a request packet
        out_payload = struct.pack('<ii', 0, out_type) + out_data.encode('utf8') + b'\x00\x00'
        out_length = struct.pack('<i', len(out_payload))
        self.socket.send(out_length + out_payload)

        # Read response packets
        in_data = ""
        while True:
            # Read a packet
            in_length, = struct.unpack('<i', self.read(4))
            in_payload = self.read(in_length)
            in_id, in_type = struct.unpack('<ii', in_payload[:8])
            in_data_partial, in_padding = in_payload[8:-2], in_payload[-2:]

            # Sanity checks
            if in_padding != b'\x00\x00':
                raise PyRconException("Incorrect padding")
            if in_id == -1:
                raise PyRconException("Login failed")

            # Record the response
            in_data += in_data_partial.decode('utf8')

            # If there's nothing more to receive, return the response
            if len(select.select([self.socket], [], [], 0)[0]) == 0:
                return in_data

    def command(self, command: str):
        result = self.send(2, command)
        time.sleep(0.003)
        return result 
        
#import timeit
#print(timeit.timeit("test_balance()", setup="from __main__ import test_balance", number=100))
#test_balance()
#sys.exit(0)

# globals
game: Game = Game()


def update_game() -> None:
    """Global tick for the log parsing functionality"""
    while True:
        game.lines_processed = game.update()
        game.infoRun = False
        time.sleep(0.25)

def parse_chat() -> None:
    time.sleep(4) # give us a chance to parse the game log
    chatfile: IO[str] = open(DEFAULT_CHAT_PATH, "r", encoding="utf-8")
    # read to the end of the file
    chatfile.seek(0, 2) # seek to end of file
    line_regex = re.compile(r'\[\d+\] (\d+): (.+)')
    while True:
        time.sleep(0.1)
        line = chatfile.readline()
        matched = line_regex.match(line)
        if matched:
            clientid = matched.group(1)
            msg = matched.group(2)
            game.on_player_message(clientid, msg)


def main(args: argparse.Namespace) -> None:
    if os.getuid() != 0:
        print("this script must run as root")
        sys.exit(1)

    Rcon.rcon_password = args.rcon_password
    Rcon.rcon_port = args.rcon_port
    
    sniff_thread = Thread(target = parse_chat)
    sniff_thread.start()

    update_thread = Thread(target = update_game)
    update_thread.start()

    game.main()

if __name__ == "__main__":
    if not os.path.exists(SERVER_LOG_PATH):
        print(f'could not find server log at path: {SERVER_LOG_PATH}')
        sys.exit(0)

    parser = argparse.ArgumentParser()
    parser.add_argument("--rcon_port", help="rcon port number", default=DEFAULT_RCON_PORT)
    parser.add_argument("--rcon_password", help="rcon password", default=DEFAULT_RCON_PASSWORD)
    parser.add_argument("--chat_path", help="path to the server chat log", default=DEFAULT_CHAT_PATH)
    args = parser.parse_args() 
    print('expecting to see server logs in: ' + SERVER_LOG_PATH)
    print('expecting to see chat logs in: ' + DEFAULT_CHAT_PATH)
    main(args)
