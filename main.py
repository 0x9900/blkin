#
try:
  import update
  update.timesfile()
except:
  print("Update error")
  pass

import belkin
belkin.main()
