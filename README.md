**Update, Jun 21**: Extended original patch so that "from client id" field is now available.

**Update, June 12**: Added another patch, enabled by default, that will skip version checking so that Mac/Linux clients can join dedicated servers even though they are stuck on the oldest version of Wargame. See https://www.reddit.com/r/wargame/comments/h0bl0h/patch_117427_teleportation_fix_bis/ for details.

One of the problems for the Wargame community is that there are some
missing features for servers -- vote to kick, vote to rotate map, broadcast
server rules, see stats about average strength of both teams compared, etc.

I've added a new rcon command, `chat <source-client-id> <client-id> <message>` which
can be used to send a message to a specific client or broadcast to the entire
lobby.

*How Do I Use This?*

1. Checkout the code: `git clone https://github.com/wargame-mods/wargame-server`
2. Use the patch script (`patch.py`) from the repo in step 1. Run `./patch.py wargame3-server`--it will produce a .patched output file.
3. Backup the old `wargame3-server` and copy the patched server in its place

If you just want the "skip version checking" functionality, that's all you need to do. But if you want the other features:

4. Run the control script: `python3.6 control.py --rcon_password=kslw48ajbscilljbnay219`
5. If you have your own scripts, change your scripts to use the new rcon command
     `chat <source-client-id> <client-id> <message>` If source-client-id is not a real client, the lobby will display the user as "??????" (default). If client-id is -1 (0xffffff), the message will be sent to all clients (this part is WIP).
6. For rx functionality, there is an undocumented command-line flag that logs all messages: 
     `+chat_log_file chat.txt`

*Features*

* Vote to kick
* Vote to rotate map
* Vote to set date restrictions
* Print lobby stats (avg level for blue vs red)
* Show server rules when game starts

*Ideas*

* set team affiliation so you can play with friends but still have autobalance. 
* autokick on $badwords
* more options to vote on forcing deck specialization
* ban excessive leave/join behavior

*Future Work*

If there's interest, it is also possible to read flare markings and probably set
them too. May also be possible to track what units are deployed at the start
(eg to enforce helorush = ban). Let me know if you have ideas!

*What are the caveats?*

Only tested on Debian jessie (8.11) and Debian stretch (9) on x86_64, but should
be pretty robust.

*How can I trust you haven't just backdoored my wargame server?*

I've distributed the patch itself, so you can verify the binary patches are
quite small and if you know how to read x86 assembly or put it through an online
decoder, you can easily see that they are not adding any backdoors.

### Credits:

Thanks to DesertEagle for the original version of the `control.py` script!
