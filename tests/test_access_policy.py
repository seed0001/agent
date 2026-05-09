from config import access_policy


def test_best_friend_can_use_send_discord_message_even_if_policy_file_missing():
    assert access_policy.is_tool_allowed("best_friend", "send_discord_message") is True


def test_good_friend_cannot_use_send_discord_message_by_default():
    assert access_policy.is_tool_allowed("good_friend", "send_discord_message") is False
