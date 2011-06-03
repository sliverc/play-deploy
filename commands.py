#!/usr/bin/env python
import subprocess
import shutil
import traceback
import time
import sys
import os

MODULE = 'deploy'

COMMANDS = ['deploy']

def execute(**kargs):
	if kargs.get('command') == 'deploy':
		deploy(kargs.get('args'), kargs.get('app'), kargs.get('env'), kargs.get('cmdloader'))
		
def deploy(args, app, env, cmdloader):
	precompile(args, app, env, cmdloader)
	for serverName in app.readConf('deploy.servers').split(','):
		Server(app, serverName).update()

def precompile(args, app, env, cmdloader):
	sys.stdout.flush()
	cmdloader.commands['precompile'].execute(command='precompile', app=app, args=args, env=env, cmdloader=cmdloader)
	sys.stdout.flush()

def log(msg):
	print msg
	sys.stdout.flush()

class Server:
	def __init__(self, app, server):
		self.app = app
		self.server = server
		self.env = app.play_env

	def update(self):
		log('updating server ' + self.server)
		self.checkHaProxy()
		self.checkPlayInstall()
		self.setFolderAndPorts()
		if not self.usingHaProxy:
			self.stop()
		self.copyApp()
		self.start()
		if self.usingHaProxy:
			self.swap()

	def checkHaProxy(self):
		self.usingHaProxy = False
                for line in self.cmd('ps -A').splitlines():
                        if line.strip().endswith((' haproxy', '\thaproxy')):
				log('Found HAProxy, will swap in new app when ready')
                                self.usingHaProxy = True
				return

	def checkPlayInstall(self):
		if not self.exists(self.getPlayRemotePath()):
			log("Didn't find Play! on server in folder " + self.getPlayRemoteDir() + ", installing now")
			self.copyDir(self.env['basedir'], self.getPlayRemoteDir())

	def exists(self, file):
		return 'nope' != self.cmd("'if test -f " + file + "; then echo \"exist\"; else echo \"nope\"; fi'").strip()

	def setFolderAndPorts(self):
		port1 = self.conf('deploy.port1', '9000')
		port2 = self.conf('deploy.port2', '9001')
		base = self.conf('deploy.base', '/opt')
		folder = os.path.join(base, self.conf('application.name', 'app'))
		folder1 = folder + '-1'
		folder2 = folder + '-2'
		if not self.usingHaProxy:
                        self.newPort = port1
                        self.oldPort = port1
                        self.folder = folder
                        self.oldFolder = folder

		elif self.appOneIsRunning(folder1):
			self.newPort = port2
			self.oldPort = port1
			self.folder = folder2
			self.oldFolder = folder1
		else:
			self.newPort = port1
			self.oldPort = port2
			self.folder = folder1
			self.oldFolder = folder2

        def appOneIsRunning(self,folder):
		path = os.path.join(folder, 'server.pid')
		if not self.exists(path):
			return False
                pid = self.cmd('cat ' + path)
		for line in self.cmd('ps -A').splitlines():
			if line.strip().startswith((pid + ' ', pid + '\t')):
				return True
		return False

	def copyApp(self):
		log('copying app to ' + self.folder)
		self.cmd('rm -fr ' + self.folder)
		self.copyDir(self.app.path, self.folder)

	def copyDir(self, src, dest):
		self.cmd('mkdir -p ' + dest)
		subprocess.call('scp -i ' + self.getPkPath() + ' -qr ' + src + '/* ' + self.getRemoteUser() + '@' + self.server + ':' + dest, shell=True)

	def getPkPath(self):
		return self.conf('deploy.pk.path', '~/.ssh/id_rsa')

	def getRemoteUser(self):
		return self.conf('deploy.user', 'root')

	def start(self):
		log('starting new app')
		self.playCmd('start', ' --http.port=' + str(self.newPort)  + ' --%' + self.env["id"] + ' -Dprecompiled=true')
		self.watchLogFile()
		log("app started, pinging to make sure it's running properly")
		self.ping(self.newPort)

	def playCmd(self,cmd,options=''):
		log(self.cmd(self.getPlayRemotePath() + ' ' + cmd + ' ' + self.folder + ' ' + options))

	def getPlayRemotePath(self):
		return os.path.join(self.getPlayRemoteDir(), 'play')

	def getPlayRemoteDir(self):
		return self.conf('deploy.play.dir', os.path.join('/opt', 'play', self.env['version']))

	def watchLogFile(self):
		log('watching log to make sure the app starts properly')
		timeWaiting = 0
		maxWaitTime = self.conf('deploy.wait.time', 60)
		while True:
			time.sleep(2)
			timeWaiting += 2
			if self.checkLogFile(timeWaiting, maxWaitTime):
				return True

	def checkLogFile(self,timeWaiting=0, maxWaitTime=0):
		logFile = os.path.join(self.folder, 'logs', 'system.out')
		contents = self.cmd('cat ' + logFile)
		if contents.find('Exception') != -1 or timeWaiting > maxWaitTime:
			self.stop()
			log('Error starting app on ' + self.server + '\nsysout contents:\n' + contents)
			raise Exception('Start Error')
		if contents.find('~ Listening for HTTP on port ') != -1:
			return True
		return False


	def stop(self):
		log('stopping old app')
		log(self.cmd(self.getPlayRemotePath() + ' stop ' + self.oldFolder))

	def ping(self, port):
		result = self.cmd('wget -q -O httpTmp localhost:' + port + self.conf('deploy.ping.path', '/'))
		self.cmd('rm httpTmp')
		self.checkLogFile()


	def cmd(self,cmd):
		cmd = 'ssh -i ' + self.getPkPath() + ' ' + self.getRemoteUser() + '@' + self.server + ' ' + cmd
		out = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE).stdout
		if(not out == None):
			return out.read().strip()

	def sudoCmd(self,cmd):
		return self.cmd('sudo ' + cmd)

	def conf(self,property, default):
		value = self.app.readConf(property)
		return value if value else default

	def swap(self):
		log('everything looks good, now swapping apps')
		self.sudoCmd('sed -i "s/:' + str(self.oldPort) + '/:' + str(self.newPort) + '/g" /etc/haproxy/haproxy.cfg')
		log(self.sudoCmd('service haproxy reload'))
		time.sleep(10)
		self.stop()

