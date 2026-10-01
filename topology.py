from mininet.net import Containernet
from mininet.node import Controller, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel

setLogLevel('info')

net = Containernet(controller=RemoteController)
net.addController('c0')

info('*** Adding mec containers\n')
mec1 = net.addDocker('mec1', ip='10.0.0.1', dcmd="python app.py", dimage="test_server:latest", mem_limit="128g",
                     cpu_quota=1000000, cpu_period=1000000)
mec2 = net.addDocker('mec2', ip='10.0.0.2', dcmd="python app.py", dimage="test_server:latest", mem_limit="128g",
                     cpu_quota=1000000, cpu_period=1000000)
mec3 = net.addDocker('mec3', ip='10.0.0.3', dcmd="python app.py", dimage="test_server:latest", mem_limit="128g",
                     cpu_quota=1000000, cpu_period=1000000)
mec4 = net.addDocker('mec4', ip='10.0.0.4', dcmd="python app.py", dimage="test_server:latest", mem_limit="128g",
                     cpu_quota=1000000, cpu_period=1000000)

info('*** Adding cloud container\n')
cloud1 = net.addDocker('cloud1', ip='10.0.2.1', dcmd="python app.py", dimage="test_server:latest", mem_limit="128g",
                       cpu_quota=1000000, cpu_period=1000000)

info('*** Adding host containers\n')
host1 = net.addDocker('host1', ip='10.0.1.1', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host2 = net.addDocker('host2', ip='10.0.1.2', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host3 = net.addDocker('host3', ip='10.0.1.3', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host4 = net.addDocker('host4', ip='10.0.1.4', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host5 = net.addDocker('host5', ip='10.0.1.5', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host6 = net.addDocker('host6', ip='10.0.1.6', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host7 = net.addDocker('host7', ip='10.0.1.7', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)
host8 = net.addDocker('host8', ip='10.0.1.8', dcmd="python app.py", dimage="test_server:latest", mem_limit="32M",
                      cpu_quota=20000, cpu_period=20000)

info('*** Setup network\n')
s1 = net.addSwitch('s1')
s2 = net.addSwitch('s2')
s3 = net.addSwitch('s3')
s4 = net.addSwitch('s4')

# Core network (high-speed links)
net.addLink(s1, s2, cls=TCLink, delay='1ms', bw=500)
net.addLink(s1, s3, cls=TCLink, delay='1ms', bw=500)
net.addLink(s1, s4, cls=TCLink, delay='1ms', bw=500)
net.addLink(s2, s3, cls=TCLink, delay='1ms', bw=500)
net.addLink(s3, s4, cls=TCLink, delay='1ms', bw=500)

# Cloud (WAN link: high delay, constrained bandwidth)
net.addLink(cloud1, s1, cls=TCLink, delay='50ms', bw=100)

# MEC servers (low latency, high bandwidth)
net.addLink(mec1, s1, cls=TCLink, delay='1ms', bw=1000)
net.addLink(mec2, s2, cls=TCLink, delay='1ms', bw=1000)
net.addLink(mec3, s3, cls=TCLink, delay='1ms', bw=1000)
net.addLink(mec4, s4, cls=TCLink, delay='1ms', bw=1000)

# IoT devices (high-speed links)
net.addLink(s1, host1, cls=TCLink, delay='1ms', bw=200)
net.addLink(s2, host2, cls=TCLink, delay='1ms', bw=200)
net.addLink(s3, host3, cls=TCLink, delay='1ms', bw=200)
net.addLink(s4, host4, cls=TCLink, delay='1ms', bw=200)
net.addLink(s1, host5, cls=TCLink, delay='1ms', bw=200)
net.addLink(s2, host6, cls=TCLink, delay='1ms', bw=200)
net.addLink(s3, host7, cls=TCLink, delay='1ms', bw=200)
net.addLink(s4, host8, cls=TCLink, delay='1ms', bw=200)


net.start()
net.pingAll()

CLI(net)

net.stop()
