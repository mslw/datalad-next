import logging
from functools import wraps
from os import environ
from pathlib import Path

import urllib

from datalad.support.external_versions import external_versions
# all datalad-core test utils needed for datalad-next
from datalad.tests.utils_pytest import (
    DEFAULT_BRANCH,
    DEFAULT_REMOTE,
    HTTPPath,
    SkipTest,
    assert_in,
    assert_in_results,
    assert_raises,
    assert_result_count,
    assert_status,
    attr,
    chpwd,
    eq_,
    get_deeply_nested_structure,
    ok_,
    ok_broken_symlink,
    ok_exists,
    ok_good_symlink,
    rmtree,
    skip_if_on_windows,
    skip_ssh,
    skip_wo_symlink_capability,
    swallow_logs,
    with_testsui,
)
from datalad.tests.test_utils_testrepos import BasicGitTestRepo
from datalad.cli.tests.test_main import run_main
from datalad.utils import (
    create_tree,
    md5sum,
)
from datalad_next.utils import (
    CredentialManager,
    optional_args,
)

lgr = logging.getLogger("datalad.tests.utils")


class WebDAVPath(object):
    """Serve the content of a path via an HTTP WebDAV URL.

    This class is a context manager.

    Parameters
    ----------
    path : str
        Directory with content to serve.
    auth : tuple
        Username, password

    Returns
    -------
    str
      WebDAV server URL
    """
    def __init__(self, path, auth=None):
        self.path = Path(path)
        self.auth = auth
        self.server = None
        self.server_thread = None

    def __enter__(self):
        try:
            from cheroot import wsgi
            from wsgidav.wsgidav_app import WsgiDAVApp
        except ImportError as e:
            raise SkipTest('No WSGI capabilities') from e

        if self.auth:
            auth = {self.auth[0]: {'password': self.auth[1]}}
        else:
            auth = True

        self.path.mkdir(exist_ok=True, parents=True)

        config = {
            "host": "127.0.0.1",
            # random fixed number, maybe make truly random and deal with taken ports
            "port": 43612,
            "provider_mapping": {"/": str(self.path)},
            "simple_dc": {"user_mapping": {'*': auth}},
        }
        app = WsgiDAVApp(config)
        self.server = wsgi.Server(
            bind_addr=(config["host"], config["port"]),
            wsgi_app=app,
        )
        lgr.debug('Starting WebDAV server')
        from threading import Thread
        self.server.prepare()
        self.server_thread = Thread(target=self.server.serve)
        self.server_thread.start()
        lgr.debug('WebDAV started')
        return f'http://{config["host"]}:{config["port"]}'

    def __exit__(self, *args):
        lgr.debug('Stopping WebDAV server')
        # graceful exit
        self.server.stop()
        lgr.debug('WebDAV server stopped, waiting for server thread to exit')
        # wait for shutdown
        self.server_thread.join()
        lgr.debug('WebDAV server thread exited')


@optional_args
def serve_path_via_webdav(tfunc, *targs, auth=None):
    """DEPRECATED: Use ``webdav_server`` fixture instead"""
    @wraps(tfunc)
    @attr('serve_path_via_webdav')
    def _wrap_serve_path_via_http(*args, **kwargs):

        if len(args) > 1:
            args, path = args[:-1], args[-1]
        else:
            args, path = (), args[0]

        with WebDAVPath(path, auth=auth) as url:
            return tfunc(*(args + (path, url)), **kwargs)
    return _wrap_serve_path_via_http


def with_credential(name, **kwargs):
    """A decorator to temporarily deploy a credential.

    If a credential of the given name already exists, it will
    be temporarily replaced by the given one.

    In pretty much all cases, the keyword arguments need to include
    `secret`. Otherwise any properties are supported.
    """
    import warnings
    warnings.warn(
        "datalad_next.tests.utils.with_credential was replaced by a `credman` "
        "fixture in datalad_next 1.0, and will be removed in "
        "datalad_next 2.0.",
        DeprecationWarning,
    )

    def with_credential_decorator(fx):
        @wraps(fx)
        def _with_credential(*dargs, **dkw):
            credman = CredentialManager()
            # retrieve anything that might be conflicting with the
            # to-be-deployed credential
            prev_cred = credman.get(name)
            try:
                credman.set(name, **kwargs)
                fx(*dargs, **dkw)
            finally:
                if prev_cred:
                    credman.set(name, **prev_cred)
                else:
                    credman.remove(name)

        return _with_credential
    return with_credential_decorator


def get_httpbin_urls():
    """Return cannonical access URLs for the HTTPBIN service

    This function checks whether a service is deployed at
    localhost:8765 and if so, it return this URL as the 'standard' URL.
    If not, a URL pointing to the cannonical instance is returned.

    For tests that need to have the service served via a specific
    protocol (https vs http), the corresponding URLs are returned
    too. They always point to the cannonical deployment, as some
    tests require both protocols simultaneously and a local deployment
    generally won't have https.
    """
    hburl = 'http://httpbin.org'
    hbsurl = 'https://httpbin.org'
    ciurl = 'http://localhost:8765'

    ci_httpbin = False
    try:
        # if we have a CI deployment of a dedicated HTTPBIN
        # it would be here
        urllib.request.urlopen(ciurl)
        ci_httpbin = True
    except urllib.error.URLError:
        pass

    return dict(
        standard=ciurl if ci_httpbin else hbsurl,
        http=hburl,
        https=hbsurl,
    )


def get_git_config_global_fpath() -> Path:
    """Returns the file path for the "global" (aka user) Git config scope"""
    fpath = environ.get('GIT_CONFIG_GLOBAL')
    if fpath is None:
        # this can happen with the datalad-core setup for Git < 2.32.
        # we provide a fallback, but we do not aim to support all
        # possible variants
        fpath = Path(environ['HOME']) / '.gitconfig'

    fpath = Path(fpath)
    return fpath
