import requests
from requests.adapters import HTTPAdapter
import time
import random
from enum import Enum
from typing import Optional, Callable, Any
import logging


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker pattern to prevent overwhelming a failing service.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service is failing, reject requests immediately
    - HALF_OPEN: Testing if service has recovered
    """

    def __init__(self, failure_threshold: int = 5, cooldown: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.failures = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function through circuit breaker"""
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.cooldown:
                logging.info("Circuit breaker entering HALF_OPEN state for testing")
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception(f"Circuit breaker is OPEN. Service unavailable. Retry in {int(self.cooldown - (time.time() - self.last_failure_time))}s")

        try:
            result = func(*args, **kwargs)
            if self.state == CircuitState.HALF_OPEN:
                logging.info("Circuit breaker test successful, resetting to CLOSED")
                self.reset()
            return result
        except Exception as e:
            self.record_failure()
            raise e

    def record_failure(self):
        """Record a failure and potentially open the circuit"""
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logging.warning(f"Circuit breaker OPENING after {self.failures} consecutive failures")
            self.state = CircuitState.OPEN

    def reset(self):
        """Reset circuit breaker to closed state"""
        self.failures = 0
        self.state = CircuitState.CLOSED


class NetworkClient:
    """
    HTTP client with connection pooling, keepalive, exponential backoff retry,
    and circuit breaker pattern.

    Features:
    - Connection pooling for reduced overhead
    - HTTP keepalive for connection reuse
    - Exponential backoff with jitter for retries
    - Circuit breaker to prevent overwhelming failing services
    - Configurable timeouts (connect vs read)
    """

    def __init__(self,
                 pool_connections: int = 10,
                 pool_maxsize: int = 20,
                 keepalive_timeout: int = 60,
                 connect_timeout: int = 10,
                 read_timeout: int = 30,
                 retry_max_attempts: int = 3,
                 retry_backoff_factor: float = 2.0,
                 retry_max_delay: int = 60,
                 circuit_breaker_threshold: int = 5,
                 circuit_breaker_cooldown: int = 60,
                 on_connection_weak: Optional[Callable] = None,
                 on_connection_lost: Optional[Callable] = None,
                 on_connection_restored: Optional[Callable] = None):
        """
        Initialize NetworkClient with configuration.

        Args:
            pool_connections: Number of connection pools to cache
            pool_maxsize: Maximum number of connections in each pool
            keepalive_timeout: How long to keep connections alive (seconds)
            connect_timeout: Timeout for establishing connection (seconds)
            read_timeout: Timeout for reading response (seconds)
            retry_max_attempts: Maximum retry attempts
            retry_backoff_factor: Exponential backoff multiplier
            retry_max_delay: Maximum delay between retries (seconds)
            circuit_breaker_threshold: Failures before opening circuit
            circuit_breaker_cooldown: Cooldown period when circuit is open (seconds)
            on_connection_weak: Callback to call when first connection failure occurs
            on_connection_lost: Callback to call when connection is completely lost
            on_connection_restored: Callback to call when connection is restored
        """
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.retry_max_attempts = retry_max_attempts
        self.retry_backoff_factor = retry_backoff_factor
        self.retry_max_delay = retry_max_delay
        self.on_connection_weak = on_connection_weak
        self.on_connection_lost = on_connection_lost
        self.on_connection_restored = on_connection_restored
        self.connection_failed = False
        self.connection_weak = False

        # Create session with connection pooling
        self.session = requests.Session()

        # Configure HTTPAdapter for keepalive and connection pooling
        adapter = HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            pool_block=False
        )

        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        # Set keepalive headers
        self.session.headers.update({
            'Connection': 'keep-alive',
            'Keep-Alive': f'timeout={keepalive_timeout}'
        })

        # Circuit breaker for each client instance
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            cooldown=circuit_breaker_cooldown
        )

    def _exponential_backoff_with_jitter(self, attempt: int) -> float:
        """
        Calculate backoff delay with exponential growth and jitter.

        Jitter prevents thundering herd problem where multiple clients
        retry at exactly the same time.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds with jitter applied
        """
        # Calculate exponential delay, capped at max_delay
        delay = min(
            self.retry_backoff_factor ** attempt,
            self.retry_max_delay
        )
        # Add random jitter (0 to 30% of delay)
        jitter = random.uniform(0, delay * 0.3)
        return delay + jitter

    def request_with_retry(self,
                          method: str,
                          url: str,
                          max_attempts: Optional[int] = None,
                          timeout: Optional[tuple] = None,
                          **kwargs) -> requests.Response:
        """
        Make HTTP request with exponential backoff retry and circuit breaker.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: URL to request
            max_attempts: Override default retry attempts
            timeout: Override default timeout as (connect, read) tuple
            **kwargs: Additional arguments to pass to requests

        Returns:
            requests.Response object

        Raises:
            Exception if all retries exhausted or circuit breaker is open
        """
        if max_attempts is None:
            max_attempts = self.retry_max_attempts

        if timeout is None:
            timeout = (self.connect_timeout, self.read_timeout)

        last_exception = None

        for attempt in range(max_attempts):
            try:
                # Use circuit breaker to protect against failing service
                response = self.circuit_breaker.call(
                    self.session.request,
                    method=method,
                    url=url,
                    timeout=timeout,
                    **kwargs
                )

                # Raise for HTTP errors (4xx, 5xx)
                response.raise_for_status()

                # Connection succeeded - restore state if we were in failed state
                if self.connection_failed or self.connection_weak:
                    self.connection_failed = False
                    self.connection_weak = False
                    if self.on_connection_restored:
                        logging.info("Connection restored")
                        self.on_connection_restored()

                return response

            except Exception as e:
                last_exception = e

                # First failure - mark connection as weak
                if attempt == 0 and not self.connection_weak and not self.connection_failed:
                    self.connection_weak = True
                    if self.on_connection_weak:
                        logging.warning("Connection weak - first failure detected")
                        self.on_connection_weak()

                if attempt < max_attempts - 1:
                    delay = self._exponential_backoff_with_jitter(attempt)
                    logging.warning(
                        f"Request to {url} failed (attempt {attempt + 1}/{max_attempts}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                else:
                    logging.error(
                        f"Request to {url} failed after {max_attempts} attempts: {e}"
                    )
                    # All retries exhausted - mark connection as completely failed
                    if not self.connection_failed:
                        self.connection_failed = True
                        self.connection_weak = False  # No longer weak, it's completely lost
                        if self.on_connection_lost:
                            logging.warning("Connection lost - all retries exhausted")
                            self.on_connection_lost()

        raise last_exception

    def get(self, url: str, max_attempts: Optional[int] = None,
            timeout: Optional[tuple] = None, **kwargs) -> requests.Response:
        """
        GET request with retry and circuit breaker.

        Args:
            url: URL to GET
            max_attempts: Override default retry attempts
            timeout: Override default timeout
            **kwargs: Additional arguments for requests.get()

        Returns:
            requests.Response object
        """
        return self.request_with_retry('GET', url, max_attempts, timeout, **kwargs)

    def post(self, url: str, max_attempts: Optional[int] = None,
             timeout: Optional[tuple] = None, **kwargs) -> requests.Response:
        """
        POST request with retry and circuit breaker.

        Args:
            url: URL to POST
            max_attempts: Override default retry attempts
            timeout: Override default timeout
            **kwargs: Additional arguments for requests.post()

        Returns:
            requests.Response object
        """
        return self.request_with_retry('POST', url, max_attempts, timeout, **kwargs)

    def get_streaming(self, url: str, max_attempts: Optional[int] = None,
                     timeout: Optional[tuple] = None, **kwargs) -> requests.Response:
        """
        GET request with streaming enabled for large downloads.
        Uses longer read timeout by default.

        Args:
            url: URL to GET
            max_attempts: Override default retry attempts
            timeout: Override default timeout (uses 60s read timeout by default)
            **kwargs: Additional arguments for requests.get()

        Returns:
            requests.Response object with streaming enabled
        """
        kwargs['stream'] = True
        # Use longer read timeout for streaming downloads
        if timeout is None:
            timeout = (self.connect_timeout, 60)
        return self.request_with_retry('GET', url, max_attempts, timeout, **kwargs)

    def close(self):
        """Close the session and cleanup connections"""
        self.session.close()
