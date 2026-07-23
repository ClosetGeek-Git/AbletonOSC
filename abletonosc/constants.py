#--------------------------------------------------------------------------------
# Constants used in AbletonOSC
#--------------------------------------------------------------------------------

#--------------------------------------------------------------------------------
# The single port the plugin's pyzmtp ROUTER binds. Replies go back to the requesting
# client over the same socket via its routing-id (so there is no separate response port).
#--------------------------------------------------------------------------------
OSC_LISTEN_PORT = 11000
OSC_ENDPOINT = "tcp://0.0.0.0:%d" % OSC_LISTEN_PORT

#--------------------------------------------------------------------------------
# ZmtpTransport (the asyncio-thread bridge) timeouts and queue size.
#
#   START_TIMEOUT_S  -- bounds the one synchronous bind at OSCServer construction
#                       (off the 100ms tick hot path).
#   CLOSE_TIMEOUT_S  -- bounds the async close (router.close + ctx.term) on shutdown.
#   JOIN_TIMEOUT_S   -- bounds joining the loop thread on shutdown.
#                       Together these are the LINGER=0 analog: no port/thread leak on
#                       Live reload.
#   INBOUND_MAXSIZE  -- bound on the tick-facing inbound queue; overflow drops the oldest
#                       message (control-rate traffic, prefer fresh requests).
#--------------------------------------------------------------------------------
START_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 2.0
JOIN_TIMEOUT_S = 2.0
INBOUND_MAXSIZE = 10000
