"""
日志管理模块 - 处理日志文件的创建、写入和关闭
"""
import os
import threading
from datetime import datetime


class TranscriptLogger:
    """字幕日志记录器"""
    
    def __init__(self):
        self.log_file = None
        self.log_lock = threading.Lock()
    
    def init_log_file(self):
        """初始化日志文件"""
        # 创建logs文件夹
        logs_dir = os.path.join(os.getcwd(), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # 生成日志文件名（当前日期时间）
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f"transcript_{timestamp}.txt"
        log_path = os.path.join(logs_dir, log_filename)
        
        # 打开日志文件
        self.log_file = open(log_path, 'w', encoding='utf-8')
        
        # 写入文件头
        self.log_file.write(f"=== Real-time Subtitle Log ===\n")
        self.log_file.write(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log_file.write(f"=" * 50 + "\n\n")
        self.log_file.flush()
        
        print(f"📝 Log file created: {log_path}")
        return log_path
    
    def write_to_log(self, tokens: list):
        """将final tokens写入日志文件"""
        if not self.log_file:
            return
        
        with self.log_lock:
            try:
                # 获取当前时间戳
                timestamp = datetime.now().strftime('%H:%M:%S')
                
                # 按说话人和语言组织tokens
                current_speaker = None
                current_lang = None
                current_translation_status = None
                current_line = []
                current_line_tokens = []  # 保存完整的token对象以便检查translation_status
                
                for token in tokens:
                    # 跳过分隔符
                    if token.get('is_separator'):
                        continue
                    
                    speaker = token.get('speaker')
                    language = token.get('language')
                    text = token.get('text', '')
                    translation_status = token.get('translation_status', 'none')
                    
                    # 如果说话人或语言改变，先写入当前行
                    if (speaker != current_speaker or language != current_lang) and current_line:
                        line_text = ''.join(current_line)
                        lang_tag = f"[{current_lang.upper()}]" if current_lang else ""
                        speaker_tag = f"[SPEAKER {current_speaker}]" if current_speaker else ""
                        status_tag = "[TRANS]" if any(t.get('translation_status') == 'translation' for t in current_line_tokens) else ""
                        
                        self.log_file.write(f"[{timestamp}] {speaker_tag}{lang_tag}{status_tag} {line_text}\n")
                        current_line = []
                        current_line_tokens = []
                    
                    current_speaker = speaker
                    current_lang = language
                    current_line.append(text)
                    current_line_tokens.append(token)
                
                # 写入最后一行
                if current_line:
                    line_text = ''.join(current_line)
                    lang_tag = f"[{current_lang.upper()}]" if current_lang else ""
                    speaker_tag = f"[SPEAKER {current_speaker}]" if current_speaker else ""
                    
                    # 检查是否包含翻译
                    status_tag = "[TRANS]" if any(t.get('translation_status') == 'translation' for t in current_line_tokens) else ""
                    
                    self.log_file.write(f"[{timestamp}] {speaker_tag}{lang_tag}{status_tag} {line_text}\n")
                
                self.log_file.flush()
                
            except Exception as e:
                print(f"Error writing to log: {e}")
    
    def close_log_file(self):
        """关闭日志文件"""
        with self.log_lock:
            if self.log_file:
                self.log_file.write(f"\n{'=' * 50}\n")
                self.log_file.write(f"Ended at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                self.log_file.close()
                self.log_file = None
                print("📝 Log file closed")
