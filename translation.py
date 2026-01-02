import asyncio
from googletrans import Translator

# 确保安装了支持异步的版本: pip install googletrans==4.0.0rc1

async def translate_google_proxy_async(text, dest='zh-cn', src='auto'):
    """
    googletrans api 翻译调用（异步版本）
    注意：中文目标语言建议使用 'zh-cn' 而不是 'zh'
    """
    async with Translator() as translator: # 4.0+ 版本建议使用上下文管理器，自动处理会话关闭
        print(f"Translating: '{text}' to {dest} (src={src})")
        try:
            # 显式指定参数名 dest=dest, src=src
            result = await translator.translate(text, dest=dest, src=src)
            print(f"Result Object: {result}")
            print(f"Result Text: {result.text}")
            return result.text
        except Exception as e:
            print(f"Error in async translation: {e}")
            return text # 失败时返回原文，或者抛出异常

def translate_google_proxy(text, dest='zh-cn', src='auto'):
    """
    同步包装器
    """
    try:
        return asyncio.run(translate_google_proxy_async(text, dest, src))
    except RuntimeError:
        # 如果由于已经在事件循环中而导致 asyncio.run 失败，尝试直接处理（视具体运行环境而定）
        print("Detected existing event loop, handling needed based on environment.")
        return text

def translate(text: str, to: str = "zh-cn", retry_times = 5):
    for i in range(retry_times):
        try:
            result = translate_google_proxy(text, to)
            # 如果翻译结果和原文一样，且原文不是空的，且目标语言不是英文（假设原文是英文），视为可能失败
            if result == text and to != 'en' and text.strip() != "":
                 print(f"Translation returned identical text, retrying... ({i+1}/{retry_times})")
                 continue
            return result
        except Exception as e:
            print(f"translate:{text}, retrying_times:{i}..., error: {e}")
    print(f"translate:{text} failed")
    return text

if __name__ == '__main__':
    # 测试 zh-cn
    print("Final Output:", translate_google_proxy("hello", "zh-cn"))