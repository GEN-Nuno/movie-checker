from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class Movie:
    """映画情報を格納するクラス"""
    title: str
    release_date: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    director: Optional[str] = None
    cast: Optional[List[str]] = None
    theaters: Optional[List[str]] = None
    source_url: Optional[str] = None
    
    def is_upcoming(self) -> bool:
        """この映画が今後上映予定かどうかを判定"""
        if not self.release_date:
            return False
        try:
            # 日付フォーマットの解析
            release = datetime.strptime(self.release_date, "%Y年%m月%d日")
            return release >= datetime.now()
        except:
            # 解析できない場合は True を返す（上映予定の可能性あり）
            return True
    
    def __str__(self):
        theaters_str = "、".join(self.theaters) if self.theaters else "情報なし"
        return f"""
【{self.title}】
公開日: {self.release_date or '未定'}
ジャンル: {self.genre or '不明'}
監督: {self.director or '不明'}
出演: {', '.join(self.cast) if self.cast else '不明'}
上映予定の映画館: {theaters_str}
概要: {self.description or '情報なし'}
情報源: {self.source_url or '不明'}
"""