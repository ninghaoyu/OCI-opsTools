"""
main 模块的单元测试
"""
import unittest
import sys
import os

# 将 src 目录添加到 Python 路径中，以便导入 main 模块
# 获取当前测试文件的目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 获取项目根目录（假设 tests 目录在根目录下）
project_root = os.path.dirname(current_dir)
# 获取 src 目录的路径
src_path = os.path.join(project_root, 'src')
# 将 src 目录添加到 sys.path
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# 现在可以导入 src 中的模块了
# pylint: disable=import-error, wrong-import-position
from main import greet

class TestMain(unittest.TestCase):
    """测试 main 模块中的函数"""

    def test_greet_success(self) -> None:
        """测试 greet 函数的成功情况"""
        self.assertEqual(greet("Alice"), "Hello, Alice!")
        self.assertEqual(greet("Bob"), "Hello, Bob!")

    def test_greet_empty_string(self) -> None:
        """测试 greet 函数传入空字符串的情况"""
        with self.assertRaises(ValueError):
            greet("")

    def test_greet_non_string(self) -> None:
        """测试 greet 函数传入非字符串的情况"""
        with self.assertRaises(ValueError):
            greet(123) # type: ignore
        with self.assertRaises(ValueError):
            greet(None) # type: ignore

if __name__ == '__main__':
    unittest.main()