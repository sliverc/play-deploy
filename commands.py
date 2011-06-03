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
	cmdloader.commands['precompile'].execute(command='precompile', app=app, args=args, env=env, cmdloader=cmdloader)

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
		self.checkPlayInstall()
		self.setFolderAndPorts()
		self.copyApp()
		self.start()
		self.swap()

	def checkPlayInstall(self):
		path = self.getPlayRemoteDir()
		exists = self.cmd("'if test -d " + path  + "; then echo \"exist\"; else echo \"nope\"; fi'").strip()
		if exists == 'nope':
			log("Didn't find Play! " + self.env['version'] + " on server, installing to " + path)
			playName = 'play-' + self.env['version']
			self.cmd('wget -q -O newPlay http://download.playframework.org/releases/' + playName  + '.zip')
			self.cmd('unzip -qq newPlay')
			self.cmd('mkdir -p ' + path)
			self.cmd('cp -r ' + playName + '/* ' + path)
			self.cmd('rm -fr ' + playName)
			self.cmd('rm newPlay')

	def setFolderAndPorts(self):
		port1 = self.conf('deploy.port1', '9000')
		port2 = self.conf('deploy.port2', '9001')
        	base = self.conf('deploy.base', '/opt')
		folder = os.path.join(base, self.conf('application.name', 'app'))
		if self.appOneIsRunning(folder + '-1'):
			self.newPort = port2
			self.oldPort = port1
			self.folder = folder + '-2'
			self.oldFolder = folder + '-1'
		else:
			self.newPort = port1
			self.oldPort = port2
			self.folder = folder + '-1'
			self.oldFolder = folder + '-2'

        def appOneIsRunning(self,folder):
                pid = self.cmd('cat ' + os.path.join(folder, 'server.pid'))
		for line in self.cmd('ps -A').splitlines():
			if line.strip().startswith((pid + ' ', pid + '\t')):
				return True
		return False

	def copyApp(self):
		log('copying app to ' + self.folder)
		self.cmd('rm -fr ' + self.folder)
		subprocess.call('scp -i ' + self.getPkPath() + ' -qr ' + self.app.path + ' ' + self.getRemoteUser() + '@' + self.server + ':' + self.folder, shell=True)

	def getPkPath(self):
		return self.conf('deploy.pk.path', '~/.ssh/id_rsa')

	def getRemoteUser(self):
		return self.conf('deploy.user', 'root')

	def start(self):
		log('starting new app on')
		self.playCmd('start', ' --http.port=' + str(self.newPort)  + ' --%' + self.env["id"] + ' -Dprecompiled=true')
		self.watchLogFile()
		log("app started, pinging to make sure it's running properly")
		self.ping(self.newPort)

	def playCmd(self,cmd,options=''):
		print self.cmd(self.getPlayRemotePath() + ' ' + cmd + ' ' + self.folder + ' ' + options)

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
			print 'Error starting app on ' + self.server + '\nsysout contents:\n' + contents
			raise Exception('Start Error')
		if contents.find('~ Listening for HTTP on port ') != -1:
			return True
		return False


	def stop(self):
		print self.cmd(self.getPlayRemotePath() + ' stop ' + self.oldFolder)

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
		log('everything looks good, now swaping servers')
		self.sudoCmd('sed -i "s/:' + str(self.oldPort) + '/:' + str(self.newPort) + '/g" /etc/haproxy/haproxy.cfg')
		log(self.sudoCmd('service haproxy reload'))
		time.sleep(10)
		log('stopping old server')
		self.stop()

