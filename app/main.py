import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QListWidget,
    QSplitter, QGroupBox, QMessageBox, QProgressBar
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from movie_scraper import MovieScraper
from movie_data import Movie


class SearchThread(QThread):
    """映画検索を別スレッドで実行"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, scraper, movie_title):
        super().__init__()
        self.scraper = scraper
        self.movie_title = movie_title
    
    def run(self):
        try:
            results = self.scraper.search_movie(self.movie_title)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class MovieSearchApp(QMainWindow):
    """映画検索アプリケーションのメインウィンドウ"""
    
    def __init__(self):
        super().__init__()
        self.scraper = MovieScraper()
        self.current_movies = []
        self.search_thread = None
        self.init_ui()
    
    def init_ui(self):
        """UIの初期化"""
        self.setWindowTitle("映画上映館検索アプリ")
        self.setGeometry(100, 100, 1000, 700)
        
        # メインウィジェット
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # メインレイアウト
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        
        # タイトル
        title_label = QLabel("🎬 映画上映館検索システム")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 検索セクション
        search_group = QGroupBox("映画を検索")
        search_layout = QHBoxLayout()
        search_group.setLayout(search_layout)
        
        search_label = QLabel("映画タイトル:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("映画のタイトルを入力してください...")
        self.search_input.returnPressed.connect(self.search_movie)
        
        self.search_button = QPushButton("🔍 検索")
        self.search_button.clicked.connect(self.search_movie)
        
        self.upcoming_button = QPushButton("📅 公開予定一覧")
        self.upcoming_button.clicked.connect(self.show_upcoming_movies)
        
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.search_button)
        search_layout.addWidget(self.upcoming_button)
        
        main_layout.addWidget(search_group)
        
        # プログレスバー
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0)  # 不定進行
        main_layout.addWidget(self.progress_bar)
        
        # 結果表示エリア
        splitter = QSplitter(Qt.Horizontal)
        
        # 左側: 検索結果リスト
        left_widget = QWidget()
        left_layout = QVBoxLayout()
        left_widget.setLayout(left_layout)
        
        list_label = QLabel("検索結果:")
        left_layout.addWidget(list_label)
        
        self.result_list = QListWidget()
        self.result_list.itemClicked.connect(self.show_movie_details)
        left_layout.addWidget(self.result_list)
        
        splitter.addWidget(left_widget)
        
        # 右側: 詳細情報
        right_widget = QWidget()
        right_layout = QVBoxLayout()
        right_widget.setLayout(right_layout)
        
        detail_label = QLabel("映画詳細情報:")
        right_layout.addWidget(detail_label)
        
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText("映画を選択すると詳細が表示されます...")
        right_layout.addWidget(self.detail_text)
        
        splitter.addWidget(right_widget)
        splitter.setSizes([300, 700])
        
        main_layout.addWidget(splitter)
        
        # ステータスバー
        self.statusBar().showMessage("映画タイトルを入力して検索してください")
        
        # スタイルシート
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
            QLineEdit {
                padding: 8px;
                border: 2px solid #ddd;
                border-radius: 4px;
            }
            QLineEdit:focus {
                border: 2px solid #4CAF50;
            }
            QListWidget {
                border: 2px solid #ddd;
                border-radius: 4px;
            }
            QTextEdit {
                border: 2px solid #ddd;
                border-radius: 4px;
                background-color: white;
            }
        """)
    
    def search_movie(self):
        """映画を検索"""
        movie_title = self.search_input.text().strip()
        
        if not movie_title:
            QMessageBox.warning(self, "入力エラー", "映画タイトルを入力してください")
            return
        
        # UI更新
        self.search_button.setEnabled(False)
        self.upcoming_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage(f"'{movie_title}' を検索中...")
        self.result_list.clear()
        self.detail_text.clear()
        
        # 検索スレッド開始
        self.search_thread = SearchThread(self.scraper, movie_title)
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.error.connect(self.on_search_error)
        self.search_thread.start()
    
    def on_search_finished(self, results):
        """検索完了時の処理"""
        self.current_movies = results
        self.progress_bar.setVisible(False)
        self.search_button.setEnabled(True)
        self.upcoming_button.setEnabled(True)
        
        if not results:
            self.statusBar().showMessage("検索結果が見つかりませんでした")
            QMessageBox.information(self, "検索結果", "該当する映画が見つかりませんでした")
            return
        
        # 結果をリストに表示
        for movie in results:
            status = "✓ 上映予定" if movie.is_upcoming() else "✗ 上映終了済"
            self.result_list.addItem(f"{status} - {movie.title}")
        
        self.statusBar().showMessage(f"{len(results)}件の映画が見つかりました")
        
        # 最初の結果を自動表示
        if results:
            self.result_list.setCurrentRow(0)
            self.show_movie_details(self.result_list.item(0))
    
    def on_search_error(self, error_msg):
        """検索エラー時の処理"""
        self.progress_bar.setVisible(False)
        self.search_button.setEnabled(True)
        self.upcoming_button.setEnabled(True)
        self.statusBar().showMessage("検索中にエラーが発生しました")
        QMessageBox.critical(self, "エラー", f"検索エラー: {error_msg}")
    
    def show_movie_details(self, item):
        """選択された映画の詳細を表示"""
        if not item:
            return
        
        index = self.result_list.row(item)
        if 0 <= index < len(self.current_movies):
            movie = self.current_movies[index]
            
            # 詳細情報を整形して表示
            details = f"""
<h2 style="color: #2c3e50;">🎬 {movie.title}</h2>

<h3 style="color: #34495e;">📅 公開情報</h3>
<p><b>公開日:</b> {movie.release_date or '未定'}</p>
<p><b>上映状況:</b> {'<span style="color: green;">上映予定</span>' if movie.is_upcoming() else '<span style="color: red;">上映終了済</span>'}</p>

<h3 style="color: #34495e;">🎭 作品情報</h3>
<p><b>ジャンル:</b> {movie.genre or '不明'}</p>
<p><b>監督:</b> {movie.director or '不明'}</p>
<p><b>出演:</b> {', '.join(movie.cast) if movie.cast else '不明'}</p>

<h3 style="color: #34495e;">🏢 上映予定の映画館</h3>
"""
            
            if movie.theaters:
                details += "<ul>"
                for theater in movie.theaters:
                    details += f"<li>{theater}</li>"
                details += "</ul>"
            else:
                details += "<p>映画館の情報がありません</p>"
            
            details += f"""
<h3 style="color: #34495e;">📝 概要</h3>
<p>{movie.description or '情報なし'}</p>

<h3 style="color: #34495e;">🔗 情報源</h3>
<p><a href="{movie.source_url}">{movie.source_url or '不明'}</a></p>

<hr>
<p style="color: #7f8c8d; font-size: 10px;">
<b>注意:</b> 上映館の情報は映画のジャンルやタイトルから推測したものです。
実際の上映情報は各映画館の公式サイトでご確認ください。
</p>
"""
            
            self.detail_text.setHtml(details)
    
    def show_upcoming_movies(self):
        """公開予定映画一覧を表示"""
        self.search_button.setEnabled(False)
        self.upcoming_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage("公開予定の映画を取得中...")
        
        try:
            upcoming = self.scraper.get_upcoming_movies()
            self.current_movies = upcoming
            self.result_list.clear()
            
            for movie in upcoming:
                self.result_list.addItem(f"📅 {movie.release_date} - {movie.title}")
            
            self.statusBar().showMessage(f"{len(upcoming)}件の公開予定映画が見つかりました")
            
            if upcoming:
                self.result_list.setCurrentRow(0)
                self.show_movie_details(self.result_list.item(0))
        
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"エラーが発生しました: {str(e)}")
        
        finally:
            self.progress_bar.setVisible(False)
            self.search_button.setEnabled(True)
            self.upcoming_button.setEnabled(True)


def main():
    """アプリケーションのエントリーポイント"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # モダンなスタイルを使用
    
    window = MovieSearchApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()