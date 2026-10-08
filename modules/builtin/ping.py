NAME = "Пинг"
MODULE_NAME = "ping"
VERSION = "1.1"
AUTHOR = "FRX"
DESCRIPTION = "Измеряет задержку ответа бота в миллисекундах"

import time
import engine
import decorators
import visual

@decorators.command('ping')
async def ping_cmd(event):
    """показывает задержку ответа в мс"""
    start_time = time.perf_counter()

    # Отправляем временное сообщение через engine.answer (если есть что редактировать - редактирует, иначе отправляет новое)
    await engine.answer(event, visual.wait('Измеряю задержку…'))

    # Вычисляем время ответа
    end_time = time.perf_counter()
    latency_ms = (end_time - start_time) * 1000

    # Редактируем сообщение с результатом (engine.answer сам отредактирует, если сообщение уже есть)
    await engine.answer(event, visual.title('Пинг') + "\n" + visual.field('задержка', f"{latency_ms:.1f} мс"))

@decorators.command('dping')
async def ping2_cmd(event):
    """показывает задержку с детальной статистикой"""
    start_time = time.perf_counter()

    # Отправляем тестовое сообщение
    await engine.answer(event, visual.wait('Тестирую задержку…'))
    send_time = time.perf_counter()

    # Редактируем сообщение
    await engine.answer(event, visual.wait('Редактирование…'))
    edit_time = time.perf_counter()

    # Отправляем ещё одно сообщение (не в ответ, а просто в чат)
    await engine.send_message(event.chat_id, visual.ok('Ответ получен'))
    reply_time = time.perf_counter()

    # Вычисляем задержки
    send_latency = (send_time - start_time) * 1000
    edit_latency = (edit_time - send_time) * 1000
    reply_latency = (reply_time - edit_time) * 1000
    total_latency = (reply_time - start_time) * 1000

    # Формируем красивый ответ (engine.answer отредактирует последнее сообщение)
    result = "\n".join([
        visual.title('Пинг', 'детальная статистика'),
        visual.field('отправка', f"{send_latency:.1f} мс"),
        visual.field('редактирование', f"{edit_latency:.1f} мс"),
        visual.field('ответ', f"{reply_latency:.1f} мс"),
        "",
        visual.field('всего', f"{total_latency:.1f} мс"),
    ])

    await engine.answer(event, result)
