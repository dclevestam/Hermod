import pathlib
import runpy


runpy.run_path(str(pathlib.Path(__file__).with_name('__main__.py')), run_name='__main__')
