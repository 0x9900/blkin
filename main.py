#
import gc

try:
  import update
  update.timesfile()
except:
  print("Update error")
  pass

gc.collect()
import belkin
belkin.main()
