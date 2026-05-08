import os
import subprocess
import discord

def media_player_control(action, target='web', source=None):
    """
    Control media playback from the user's library on desktop, web app, or Discord.
    Parameters:
      - action (str): 'play', 'pause', 'stop', 'next', 'previous', or 'join_voice' for Discord.
      - target (str): 'web' for web app playback or 'discord' for streaming in a channel.
      - source (str): Path to the music file or library folder (optional for play).
    Returns:
      - str: Status message indicating success or failure.
    """
    if target == 'web':
        if action == 'play':
            if source:
                if os.path.exists(source):
                    try:
                        # Placeholder for web app media player API call or desktop control
                        return f'Playing {source} on web app.'
                    except Exception as e:
                        return f'Error playing {source} on web app: {str(e)}'
                else:
                    return f'Source file {source} not found.'
            return 'No source provided for playback on web app.'
        elif action in ['pause', 'stop', 'next', 'previous']:
            # Placeholder for controlling playback on web app
            return f'{action.capitalize()} action executed on web app.'
        else:
            return f'Unsupported action {action} for web app.'
    
    elif target == 'discord':
        if action == 'join_voice':
            try:
                # Placeholder for Discord bot voice channel integration
                return 'Joined Discord voice channel. Ready to stream audio.'
            except Exception as e:
                return f'Error joining Discord voice channel: {str(e)}'
        elif action == 'play':
            if source:
                if os.path.exists(source):
                    try:
                        # Placeholder for streaming audio in Discord voice channel
                        return f'Streaming {source} in Discord voice channel.'
                    except Exception as e:
                        return f'Error streaming {source} in Discord: {str(e)}'
                else:
                    return f'Source file {source} not found.'
            return 'No source provided for streaming in Discord.'
        elif action in ['pause', 'stop', 'next', 'previous']:
            # Placeholder for controlling playback in Discord
            return f'{action.capitalize()} action executed in Discord voice channel.'
        else:
            return f'Unsupported action {action} for Discord.'
    
    return 'Invalid target specified. Use "web" or "discord".'

if __name__ == "__main__":
    # Example test
    print(media_player_control('play', 'web', 'C:/Users/aztre/Music/sample.mp3'))
    print(media_player_control('join_voice', 'discord'))