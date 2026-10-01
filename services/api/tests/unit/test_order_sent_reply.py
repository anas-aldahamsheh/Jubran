"""Once an order went through, the reply is the model's own words, but never a wrong order number."""
from jubran.application.ai.agent_service import confirmed_order_reply


def test_the_models_words_are_kept():
    assert confirmed_order_reply("تمام، طلبك وصل المطبخ!", "JB-104", "ar") == "تمام، طلبك وصل المطبخ!"
    assert confirmed_order_reply("وصل طلبك JB-104 للمطعم", "JB-104", "ar") == "وصل طلبك JB-104 للمطعم"


def test_a_wrong_or_missing_reply_gives_the_servers_sentence():
    assert confirmed_order_reply("طلبك JB-101 بالطريق", "JB-104", "ar").startswith("تم إرسال طلبك رقم JB-104")
    assert confirmed_order_reply("", "JB-104", "en").startswith("Your order JB-104")
