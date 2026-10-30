import requests
from bs4 import BeautifulSoup
from typing import List, Optional
from movie_data import Movie
import re


class MovieScraper:
    """映画情報をWeb上から取得するクラス"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # 主要映画館チェーン（推測用）
        self.major_theaters = [
            "TOHOシネマズ",
            "イオンシネマ",
            "109シネマズ",
            "ユナイテッド・シネマ",
            "MOVIXシネマ",
            "コロナシネマワールド",
            "ティ・ジョイ",
            "新宿ピカデリー",
            "丸の内TOEI",
            "シネマサンシャイン"
        ]
    
    def search_movie(self, movie_title: str) -> List[Movie]:
        """
        映画タイトルを検索し、情報を取得
        
        実際のスクレイピングは各サイトの利用規約を確認の上実装してください。
        ここではダミーデータと推測ロジックを含めています。
        """
        results = []
        
        # 映画.comでの検索（ダミー実装）
        movie_com_result = self._search_eiga_com(movie_title)
        if movie_com_result:
            results.extend(movie_com_result)
        
        # Yahoo!映画での検索（ダミー実装）
        yahoo_result = self._search_yahoo_movies(movie_title)
        if yahoo_result:
            results.extend(yahoo_result)
        
        return results
    
    def _search_eiga_com(self, title: str) -> List[Movie]:
        """
        映画.comから情報を取得（デモ実装）
        
        実際の実装では、映画.comのAPIまたはスクレイピングを使用
        """
        try:
            # 注意: 実際のスクレイピングを行う場合は、利用規約を確認してください
            # ここではデモとして構造を示しています
            
            search_url = f"https://eiga.com/search/?keyword={requests.utils.quote(title)}"
            
            # デモ用のダミーデータ
            movies = []
            
            # タイトルに基づいて推測される映画館を決定
            predicted_theaters = self._predict_theaters(title)
            
            # ダミーの映画情報を作成
            movie = Movie(
                title=title,
                release_date="2025年12月01日",  # ダミー日付
                description=f"「{title}」の情報を検索中... 実際のデータは映画情報サイトから取得されます。",
                genre="未定",
                director="情報取得中",
                cast=["出演者情報取得中"],
                theaters=predicted_theaters,
                source_url=search_url
            )
            movies.append(movie)
            
            return movies
            
        except Exception as e:
            print(f"映画.com検索エラー: {e}")
            return []
    
    def _search_yahoo_movies(self, title: str) -> List[Movie]:
        """
        Yahoo!映画から情報を取得（デモ実装）
        """
        try:
            search_url = f"https://movies.yahoo.co.jp/search?q={requests.utils.quote(title)}"
            
            predicted_theaters = self._predict_theaters(title)
            
            movie = Movie(
                title=f"{title} (Yahoo!映画)",
                release_date="2025年11月15日",  # ダミー日付
                description="Yahoo!映画からの情報取得中...",
                genre="未定",
                theaters=predicted_theaters,
                source_url=search_url
            )
            
            return [movie]
            
        except Exception as e:
            print(f"Yahoo!映画検索エラー: {e}")
            return []
    
    def _predict_theaters(self, title: str) -> List[str]:
        """
        映画タイトルから上映される可能性のある映画館を推測
        
        推測ロジック:
        - 大作・メジャー作品: 全国チェーン
        - アニメ: アニメに強いチェーン
        - 洋画: 大型シネコン
        - 邦画: 幅広く
        - ミニシアター系: 専門館
        """
        predicted = []
        
        title_lower = title.lower()
        
        # アニメ・漫画原作の判定
        if any(keyword in title for keyword in ["アニメ", "劇場版", "ガンダム", "ワンピース", "ドラゴンボール", "ポケモン"]):
            predicted.extend([
                "TOHOシネマズ（全国）",
                "イオンシネマ（全国）",
                "109シネマズ（主要都市）",
                "ユナイテッド・シネマ（全国）"
            ])
        
        # ディズニー・ピクサー作品
        elif any(keyword in title_lower for keyword in ["disney", "pixar", "ディズニー", "ピクサー"]):
            predicted.extend([
                "TOHOシネマズ（全国）",
                "イオンシネマ（全国）",
                "109シネマズ（全国）",
                "ユナイテッド・シネマ（全国）",
                "MOVIXシネマ（全国）"
            ])
        
        # マーベル・DC作品
        elif any(keyword in title_lower for keyword in ["marvel", "マーベル", "アベンジャーズ", "スパイダーマン", "バットマン"]):
            predicted.extend([
                "TOHOシネマズ（全国・IMAX対応館あり）",
                "109シネマズ（主要都市・IMAX対応館あり）",
                "ユナイテッド・シネマ（全国）",
                "イオンシネマ（全国）"
            ])
        
        # 大作・話題作（仮定）
        else:
            predicted.extend([
                "TOHOシネマズ（全国）",
                "イオンシネマ（全国）",
                "109シネマズ（主要都市）",
                "ユナイテッド・シネマ（全国）",
                "MOVIXシネマ（主要都市）",
                "シネマサンシャイン（地域により）"
            ])
        
        # ミニシアター系も追加
        if len(title) > 10 or any(keyword in title for keyword in ["ドキュメンタリー", "インディーズ"]):
            predicted.append("アップリンク渋谷・吉祥寺（ミニシアター）")
            predicted.append("新宿武蔵野館（ミニシアター）")
        
        return predicted if predicted else ["情報不足のため推測不可"]
    
    def get_upcoming_movies(self) -> List[Movie]:
        """
        公開予定の映画一覧を取得（デモ実装）
        """
        # 実際の実装では、映画情報サイトから取得
        sample_movies = [
            Movie(
                title="サンプル映画A",
                release_date="2025年11月20日",
                description="これから公開される大作映画",
                genre="アクション",
                theaters=self._predict_theaters("アクション大作"),
                source_url="https://eiga.com/"
            ),
            Movie(
                title="サンプル映画B",
                release_date="2025年12月05日",
                description="感動のヒューマンドラマ",
                genre="ドラマ",
                theaters=self._predict_theaters("ドラマ"),
                source_url="https://movies.yahoo.co.jp/"
            ),
        ]
        return sample_movies