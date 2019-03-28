#!/usr/bin/env python3.6
# coding=utf-8
"""

    Wargame Server Control Script
    Original Author: DesertEagle
    Modified By: kissinger
    
    Requirements: 
        pip3 install python-geoip-python3 python-geoip-geolite2 scapy

    Type-checking:
        mypy control.py --disallow-any-generics --no-implicit-optional --disallow-incomplete-defs --disallow-untyped-defs --disallow-untyped-calls --disallow-any-generics --strict --warn-return-any --warn-redundant-casts --warn-unused-ignores --no-warn-no-return

"""

from geoip import geolite2 # type: ignore

from typing import Tuple, Pattern, List, Iterable, Optional, Callable, Any, Dict, Match, IO
import argparse
import re
import os
import select, sys
from time import sleep
import time
from subprocess import call
from enum import Enum
from random import random
from math import floor
from threading import Thread
import collections
import queue
DIR_PATH = os.path.dirname(os.path.realpath(__file__))

#================================================================================#
# environment parameters 
#================================================================================#
DEFAULT_RCON_PORT = '10842'
DEFAULT_RCON_PATH = "/srv/wargame/wargame3_server/mcrcon/mcrcon"
DEFAULT_RCON_PASSWORD = 'rcon_password'
WARGAME_PORT = 10001
DEFAULT_CHAT_PATH = "chat.txt"
SERVER_LOG_PATH = "serverlog.txt"

#================================================================================#
# your specific lobby's parameters 
#================================================================================#
MIN_PLAYER_LEVEL = 5
LOBBY_RULES = '[EXPERIMENTAL, type "command" for more commands] server rules: strictly no teamkilling (even in self-defense); mark starting zones with flare or chat; minimum player level: ' + str(MIN_PLAYER_LEVEL)
MIN_VOTES_TO_KICK = 5
MIN_VOTES_TO_ROTATE = 3
MIN_VOTES_TO_YEAR = 3
GENERAL_BLUE_DECK = "XuAVOOkCbkxlBEyoMkgTf1Il1KtJYkaaQ9JaVnSbFS0syQUqwUlT/FVELI6A1nLhNYKTUsil9ScaLGLg"
GENERAL_RED_DECK = "tOAcF6LTLwXEYZMocldI1qnDBZdjgqZZZKW4aUMuHEbSSRMWR2SyIWytaL9KelYE/A=="
MAP_POOL = [
    "Destruction_2x2_port_Wonsan_Terrestre",
    "Destruction_2x3_Hwaseong",
    "Destruction_2x3_Esashi",
    "Destruction_2x3_Boseong",
    "Destruction_2x3_Tohoku",
    "Destruction_2x3_Anbyon",
    "Destruction_3x2_Boryeong_Terrestre",
    "Destruction_3x2_Taean",
    "Destruction_3x2_Taebuko",
    "Destruction_3x2_Sangju",
    "Destruction_3x2_Montagne_3",
    "Destruction_3x3_Muju",
    "Destruction_3x3_Pyeongtaek",
    "Destruction_3x3_Gangjin"
]

#================================================================================#
# general constants
#================================================================================#
YEAR_MAP = { '1985': 0, '1980': 1, 'any': -1 }
COMMAND_LIST = 'try chatting: stats, kick <player-name>, rotate, rules, wherefrom, year <1980, 1985, any>'

def update_game() -> None:
    """Global tick for the log parsing functionality"""
    while True:
        game.update()
        time.sleep(0.25)

def parse_chat() -> None:
    chatfile: IO[str] = open(DEFAULT_CHAT_PATH, "r", encoding="utf-8")
    # read to the end of the file
    chatfile.seek(0, 2) # seek to end of file
    line_regex = re.compile('\[\d+\] (\d+): (.+)')
    while True:
        line = chatfile.readline()
        matched = line_regex.match(line)
        if matched:
            clientid = matched.group(1)
            msg = matched.group(2)
            game.on_player_message(clientid, msg)

class Side(Enum):
    Bluefor = 0
    Redfor = 1

class GameState(Enum):
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
        self.votes: Dict[str, Dict[Any, bool]] = { 'kick': {}, 'rotate': {}, 'year': {} }

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
        return self._name

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
        Rcon.execute("setpvar " + self._id + " PlayerAlliance " + str(side))
    
    def change_side(self, side: int) -> None:
        """Forcibly change player's side"""
        Rcon.execute("setpvar " + self._id + " PlayerAlliance " + str(side))

    def change_deck(self, deck: str) -> None:
        """Forcibly assign new deck to a player"""
        Rcon.execute("setpvar " + self._id + " PlayerDeckContent " + deck)

    def kick(self) -> None:
        """Kick player"""
        Rcon.execute("kick " + self._id)

    def ban(self) -> None:
        """Ban player"""
        Rcon.execute("ban " + self._id)


class Server:
    """
    Server data structure
    Incapsulates server manipulation
    """
    @classmethod
    def send_message(self, message: str, only_to_client_id:Optional[str]=None) -> None:
        """Send a message. If not client specified, will go to all clients"""
        if not only_to_client_id:
            client_id_hex = 0xffffffff # broadcast
        else:
            client_id_hex = int(only_to_client_id)
            assert client_id_hex < 0xffffffff

        # TODO: this is not working yet in the patched binary, so we're just always setting it to the client id. remove this line once it works.
        client_id_hex = 0xffffffff
            
        # strip the 0x prefix on the hex client id
        Rcon.execute("chat " + ('%08x' % client_id_hex) + " " + message)
        
    @classmethod
    def change_map(cls, mapname: str) -> None:
        Rcon.execute("setsvar Map " + mapname)

    @classmethod
    def change_name(cls, name: str) -> None:
        Rcon.execute("setsvar ServerName " + name)

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
    rcon_path: str = DEFAULT_RCON_PATH
    rcon_host: str = "localhost"
    rcon_port: str = DEFAULT_RCON_PORT
    rcon_password: str = DEFAULT_RCON_PASSWORD

    @classmethod
    def execute(cls, command: str) -> None:
        """Execute rcon command, incapsulating details"""
        execution_string = cls.rcon_path + ' -H ' + cls.rcon_host + ' -P ' + cls.rcon_port + \
            ' -p ' + cls.rcon_password + ' "' + command + '"'
        call(execution_string, shell=True)

class Game:
    """Main class, containing game process manipulation"""
    last_message: Optional[str] = None    
    
    # -------------------------------------------
    # User event handlers
    # -------------------------------------------
    
    def on_player_connect(self, playerid: str) -> None:
        pass #Server.send_message(LOBBY_RULES, playerid)

    def on_player_deck_set(self, playerid: str, playerdeck: str) -> None:
        pass

    def on_player_message(self, client_id: str, msg: str) -> None:
        # find the player id
        from_player = self.players.get(client_id)
        if not from_player:
            print('error: player not found for id: ' + client_id)
            return
                    
        print('[' + str(from_player.get_id()) + ':' + from_player.get_name() + ']: ' + msg)
        if msg == 'rules':
            print('sending rules')
            Server.send_message(LOBBY_RULES, from_player.get_id())
        elif msg.startswith('kick'):
            self.handle_kick_request(msg, from_player)
        elif msg == 'stats':
            self.message_average_team_info(True)
        elif msg == 'commands':
            Server.send_message(COMMAND_LIST)
        elif msg == 'rotate':
            self.handle_rotate_request(from_player)
        elif msg.startswith('year'):
            self.handle_year_request(msg, from_player)
        elif msg == 'wherefrom':
            s = []
            for player in self.players.values():
                match = geolite2.lookup(player.get_ip())
                if match:                    
                    s.append(player.get_name() + ': ' + match.country)
            Server.send_message(', '.join(s))


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
        Server.send_message(LOBBY_RULES)

    def on_switch_to_debriefing(self) -> None:
        self.map_random_rotate()

    def on_switch_to_deployment(self) -> None:
        Server.send_message(LOBBY_RULES)

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

    def find_player_id_by_name(self, name: str) -> Optional[Player]:
        for player in self.players.values():
            if player.get_name() == name:
                return player
        return None

    def find_player_id_by_ip(self, ip: str, port: int) -> Optional[Player]:
        for playerID, player in self.players.items():
            if player.get_ip() == ip and player.get_port() == port:
                return player
        return None
            
    def handle_rotate_request(self, from_player: Player) -> None:
        from_player.votes['rotate'][1] = True
        nvotes = self.count_votes('rotate', 1, same_team=False)
        Server.send_message(str(nvotes) + '/' + str(MIN_VOTES_TO_ROTATE) + ' votes to rotate')
        if nvotes >= MIN_VOTES_TO_ROTATE:
            self.map_random_rotate()
            for player in self.players.values():
                player.votes['rotate'] = {}

    def handle_year_request(self, msg: str, from_player: Player) -> None:
        year = msg.split(' ')[1]
        if year not in YEAR_MAP:
            Server.send_message("Unknown year, options are: " + ', '.join(YEAR_MAP.keys()))
            return
        from_player.votes['year'][year] = True
        nvotes = self.count_votes('year', year, same_team=False)
        Server.send_message(str(nvotes) + '/' + str(MIN_VOTES_TO_YEAR) + ' votes to set year to: ' + year)
        if nvotes >= MIN_VOTES_TO_YEAR:
            Server.change_date_constraint(YEAR_MAP[year])
            # after that, we need to force all the decks -- this kicks people with the wrong year though!
            # self.assign_decks()
            for player in self.players.values():
                player.votes['year'] = {}
                
    def handle_kick_request(self, msg: str, from_player: Player) -> None:
        parts = msg[len('kick '):]
        kickable_player = self.find_player_id_by_name(parts)
        if kickable_player:
            from_player.votes['kick'][kickable_player.get_id()] = True
            nvotes = self.count_votes('kick', kickable_player.get_id(), same_team=True)
            Server.send_message(str(nvotes) + '/' + str(MIN_VOTES_TO_KICK) + ' votes from same team to kick ' + kickable_player.get_name())
            if nvotes >= MIN_VOTES_TO_KICK:
                kickable_player.kick()
                for player in self.players.values():
                    if kickable_player.get_id() in player.votes['kick']:
                        del player.votes['kick'][kickable_player.get_id()]
        else:
            Server.send_message('player ' + parts + ' not found')
    
    def assign_decks(self) -> None:
        """Forcing specific deck usage"""
        for playerID, player in self.players.items():
            if player.get_side() == Side.Bluefor:
                if player.get_deck() != GENERAL_BLUE_DECK:
                    player.change_deck(GENERAL_BLUE_DECK)

            if player.get_side() == Side.Redfor:
                if player.get_deck() != GENERAL_RED_DECK:
                    player.change_deck(GENERAL_RED_DECK)

    def map_random_rotate(self) -> None:
        """Rotate maps from the pool"""
        self.currentMapId = floor(len(MAP_POOL) * random())
        Server.change_map(MAP_POOL[self.currentMapId])
        print("Rotating map to " + MAP_POOL[self.currentMapId])

    def limit_level(self, playerid: str, playerlevel: int) -> None:
        """Kick players below certain level"""
        limit = MIN_PLAYER_LEVEL
        if playerlevel < limit:
            msg = ("Player (" + playerid + ") level is too low: " + str(playerlevel) + ". Min is " + str(limit) + ". Kicking...")
            print(msg)
            Server.send_message(msg)
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
            self.players[playerid] = Player(playerid, player_ip, int(player_port))

        if not self.infoRun:
            self.on_player_connect(playerid)

    # ----------------------------------------------
    def _on_player_deck_set(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        playerdeck = match_obj.group(2)

        self.players[playerid].set_deck(playerdeck)

        if not self.infoRun:
            self.on_player_deck_set(playerid, playerdeck)

    # ----------------------------------------------
    def _on_player_level_set(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        playerlevel = match_obj.group(2)

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

        if not self.infoRun:
            self.on_player_disconnect(playerid)

        if playerid in self.players:
            del self.players[playerid]

    # ----------------------------------------------
    def _on_player_side_change(self, match_obj: Match[str]) -> None:
        playerid = match_obj.group(1)
        side = Side.Redfor if match_obj.group(2) == '1' else Side.Bluefor
        self.players[playerid].set_side(side)

        if not self.infoRun:
            self.on_player_side_change(playerid, side)

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
            
    # ---------------------------------------------
    # Event handlers registration
    # ---------------------------------------------

    def register_events(self) -> None:
        self.register_event('Client added in session \(EugNetId : ([0-9]+).+IP : ([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}):([0-9]+)', self._on_player_connect)
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

    # -------------------------------------------
    # Utility functions
    # -------------------------------------------

    def __init__(self) -> None:
        self.events: Dict[Pattern[str], Callable[[Match[str]], None]] = {}
        self.players: Dict[str, Player] = {}
        self.gameState: GameState = GameState.Lobby
        self.logfileStream: IO[str] = open(SERVER_LOG_PATH, "r", encoding="utf-8")
        self.infoRun: bool = True
        self.register_events()
        self.currentMapId = -1
        self.tick_count = 0

        # Getting starting line
        while True:
            line = self.logfileStream.readline()
            if not line:
                # 0 player line is not found, reseting to the start of file
                self.logfileStream.seek(0, os.SEEK_SET)
                break

            if line == u"Variable NbPlayer set to \"0\"\n":
                # 0 player line is found, keeping this state of the stream
                break

    def __del__(self) -> None:
        self.logfileStream.close()

    def main(self) -> None:
        print("Server control script started")
        print("Gather information run")

        self.update()

        print("Gather information run is over")
        self.infoRun = False

        print('Server control started, type "help" for help')
        while True:
            self.run_cli()
            #self.dump_state()
            self.message_average_team_info()
            if self.tick_count % 60 == 0:
                Server.send_message('chat "commands" for a list of commands')
            self.tick_count += 1


    def run_cli(self) -> None:
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
        else:
            print('')

    def average_player_level(self, players: Iterable[Player], side: Side) -> float:
        acc = 0
        count = 0
        for player in players:
            if player.get_side() == side:
                acc += player.get_level()
                count += 1
        if count == 0:
            return 0
        return float(acc) / float(count)

    def message_average_team_info(self, force: bool=False) -> None: 
        blue = 'avg blue: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Bluefor))
        red = 'avg red: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Redfor))
        msg = blue + " - " + red
        if msg != self.last_message or force:
            Server.send_message(msg)
        self.last_message = msg
    
    def dump_state(self) -> None:
        #print(chr(27) + "[2J")
        
        print("We have {} players:".format(len(self.players)))
        for player in sorted(self.players.values(), key=lambda x: str(x.get_side())):
            print('[{}] {}:\t{}\t{}'.format(str(player.get_side()), str(player.get_level()), player.get_id(), player.get_name()))
        print('-------------')
        print('avg blue: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Bluefor)))
        print('avg red: {:.2f}'.format(self.average_player_level(self.players.values(), Side.Redfor)))
        print('-------------')

    def register_event(self, regex: str, handler: Callable[[Match[str]], None]) -> None:
        """Register event handler for a certain log entry"""
        self.events[re.compile(regex)] = handler

    def update(self) -> None:
        """Parse log and trigger event handler"""
        while True:
            line = self.logfileStream.readline()
            if line:
                # Test against event expressions
                for pair in self.events.items():
                    match = pair[0].match(line)
                    if match:
                        pair[1](match)
                        break
            else:
                break

def main(args: argparse.Namespace) -> None:
    if os.getuid() != 0:
        print("this script must run as root")
        sys.exit(1)

    Rcon.rcon_password = args.rcon_password
    Rcon.rcon_port = args.rcon_port
    Rcon.rcon_path = args.rcon_path
    
    sniff_thread = Thread(target = parse_chat)
    sniff_thread.start()
    update_thread = Thread(target = update_game)
    update_thread.start()
    game.main()

# globals
game: Game = Game()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rcon_port", help="rcon port number", default=DEFAULT_RCON_PORT)
    parser.add_argument("--rcon_path", help="rcon path", default=DEFAULT_RCON_PATH)
    parser.add_argument("--rcon_password", help="rcon password", default=DEFAULT_RCON_PASSWORD)
    parser.add_argument("--chat_path", help="path to the server chat log", default=DEFAULT_CHAT_PATH)
    args = parser.parse_args() 
    print('expecting to see server logs in :' + SERVER_LOG_PATH)
    print('expecting to see chat logs in :' + DEFAULT_CHAT_PATH)
    main(args)
